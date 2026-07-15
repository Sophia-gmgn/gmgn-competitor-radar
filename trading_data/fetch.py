#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品交易数据（#6 · stan）—— 抓取（DefiLlama 交易量 + Dune 用户数）。

每家怎么取（见 config.yaml 的 roster）：
  vol_slug              -> DefiLlama /summary/dexs（全链交易量）
  fees_slug + fee_rate  -> DefiLlama /summary/fees 的手续费 ÷ 费率，估算交易量（如 Maestro=1%）
  两者都无              -> 暂无公开数据源，不抓取（页面按 roster 留空「暂无」）
"""
import os
import sys
import json
import pathlib
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
from common.util import load_dotenv, enable_truststore, load_config, today_cst

DATA_FILE = os.environ.get("TRADING_DATA_FILE", "data/trading_data.json")
LLAMA = "https://api.llama.fi"


def _sum_last_n(chart, n):
    if not chart:
        return None
    vals = [v for _, v in chart[-n:] if isinstance(v, (int, float))]
    return sum(vals) if vals else None


def _fetch_volume(client, slug):
    """DefiLlama dexs 全链交易量。"""
    url = f"{LLAMA}/summary/dexs/{slug}?excludeTotalDataChartBreakdown=true"
    try:
        r = client.get(url, timeout=40)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or d.get("total24h") is None:
            return None
        chart = d.get("totalDataChart") or []
        return {"d1": d.get("total24h"), "d7": d.get("total7d"),
                "d14": _sum_last_n(chart, 14), "d30": d.get("total30d"),
                "name": d.get("name"), "chains": d.get("chains") or [], "via": "dexs"}
    except Exception:
        return None


def _fetch_fees_volume(client, slug, fee_rate):
    """无 dexs 数据的竞品：用手续费 ÷ 费率 估算交易量（如 Maestro，费率 1%）。"""
    rate = fee_rate or 0.01
    url = f"{LLAMA}/summary/fees/{slug}?excludeTotalDataChartBreakdown=true"
    try:
        r = client.get(url, timeout=40)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or d.get("total24h") is None:
            return None
        chart = d.get("totalDataChart") or []

        def vol(x):
            return (x / rate) if isinstance(x, (int, float)) else None

        return {"d1": vol(d.get("total24h")), "d7": vol(d.get("total7d")),
                "d14": vol(_sum_last_n(chart, 14)), "d30": vol(d.get("total30d")),
                "name": d.get("name"), "chains": d.get("chains") or [], "via": "fees"}
    except Exception:
        return None


def fetch_all(cfg):
    roster = cfg.get("roster", []) or []
    out = []
    with httpx.Client(headers={"User-Agent": "gmgn-radar/1.0"}) as client:
        for c in roster:
            label = c.get("label") or ""
            is_self = bool(c.get("self"))
            tier = c.get("tier") or "minor"
            sol_only = bool(c.get("sol_only"))
            vol_slug = str(c.get("vol_slug") or "").strip()
            fees_slug = str(c.get("fees_slug") or "").strip()

            vol, slug = None, None
            if vol_slug:
                vol, slug = _fetch_volume(client, vol_slug), vol_slug
            elif fees_slug:
                vol, slug = _fetch_fees_volume(client, fees_slug, c.get("fee_rate")), fees_slug

            if not vol:
                if vol_slug or fees_slug:
                    print(f"  [{label}] 无交易量数据（slug={vol_slug or fees_slug}）")
                continue  # 无数据源（Based Bot / DeBot）不抓，页面按 roster 留空

            out.append({"label": label, "slug": slug, "self": is_self,
                        "tier": tier, "sol_only": sol_only,
                        "via": vol.get("via"), "vol": vol})
            v1 = vol.get("d1") or 0
            v30 = vol.get("d30") or 0
            tag = "（估算·手续费）" if vol.get("via") == "fees" else ("（仅Solana）" if sol_only else "")
            print(f"  [{label}] 24h=${v1:,.0f}  30d=${v30:,.0f} {tag}")
    return out


def _load_prev(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


WINDOWS = ("users_today", "users_7d", "users_14d", "users_30d")


def _fetch_users(cfg):
    """Dune 活跃用户（全链口径）。

    Solana 主查询（每天强制刷新）：每家 Solana 活跃 + 核心/次要 Solana 去重合计（层级行 bot 以「【」开头）。
    EVM 合并查询（每周一强制刷新，其余取缓存不花额度）：Banana Gun / Maestro 的 EVM 活跃。
    合成：每家 全链 = Solana + EVM；层级全链合计 = 该层 Solana 去重合计 + 该层各家 EVM 相加。
    返回 (users_list, tier_totals_dict)。
    """
    sol_qid = cfg.get("dune_users_query_id")
    evm_qid = cfg.get("dune_evm_users_query_id")
    bot_map = cfg.get("dune_bot_map") or {}
    if not (sol_qid and os.environ.get("DUNE_API_KEY", "").strip()):
        print("\n（未配置 DUNE_API_KEY 或 query id，跳过用户数）")
        return [], {}

    from common.dune import get_query_result

    # ---- Solana（每天强制刷新，拿当天最新）----
    per_bot, sol_tier = {}, {}
    try:
        sol_rows = get_query_result(int(sol_qid), refresh=True)
    except Exception as e:
        print(f"\n Dune Solana 用户数抓取失败（不影响交易量）：{e}")
        return [], {}
    for r in sol_rows:
        raw = str(r.get("bot", "")).strip()
        tier = (str(r.get("tier", "")).strip() or "minor")
        counts = {w: r.get(w) for w in WINDOWS}
        if raw.startswith("【"):                 # 层级去重合计行
            sol_tier[tier] = counts
        else:
            per_bot[bot_map.get(raw, raw)] = {"tier": tier, "sol": counts}

    # ---- EVM（每周一强制刷新，其余取缓存）----
    evm_per_bot = {}
    if evm_qid and int(evm_qid) > 0:
        is_monday = datetime.now(timezone.utc).weekday() == 0
        try:
            for r in get_query_result(int(evm_qid), refresh=is_monday):
                label = bot_map.get(str(r.get("bot", "")).strip(), str(r.get("bot", "")).strip())
                evm_per_bot[label] = {w: r.get(w) for w in WINDOWS}
            print(f"\nEVM 用户数（Dune · {'本周已刷新' if is_monday else '取缓存'}）：{len(evm_per_bot)} 家")
        except Exception as e:
            print(f"\n EVM 用户数抓取失败（用 Solana 口径继续）：{e}")

    # ---- 合成每家全链 ----
    users = []
    for label, d in per_bot.items():
        sol, evm = d["sol"], evm_per_bot.get(label)
        combined = {}
        for w in WINDOWS:
            s, e = sol.get(w), (evm or {}).get(w)
            combined[w] = ((s or 0) + (e or 0)) if (s is not None or e is not None) else None
        users.append({"label": label, "tier": d["tier"], **combined,
                      "sol": sol, "evm": evm,
                      "chain_note": ("Sol+EVM" if evm else "Solana")})
    users.sort(key=lambda x: x.get("users_30d") or 0, reverse=True)

    # ---- 层级全链合计 ----
    tier_totals = {}
    for tier in ("core", "minor"):
        base = sol_tier.get(tier)
        if not base:
            continue
        evm_add = {w: 0 for w in WINDOWS}
        for label, d in per_bot.items():
            if d["tier"] == tier and evm_per_bot.get(label):
                for w in WINDOWS:
                    evm_add[w] += (evm_per_bot[label].get(w) or 0)
        tot = {w: (base.get(w) or 0) + evm_add[w] for w in WINDOWS}
        tier_totals[tier] = {**tot, "sol_dedup": base, "evm_added": evm_add}

    print(f"\n用户数（全链）：{len(users)} 家")
    for u in users:
        print(f"  [{u['label']}] 30d全链={u.get('users_30d')}  ({u['chain_note']})")
    for tier, t in tier_totals.items():
        print(f"  【{tier}·全链去重合计】30d={t.get('users_30d')}")
    return users, tier_totals


def main():
    load_dotenv()
    enable_truststore()
    cfg = load_config().get("trading_data", {}) or {}
    roster = cfg.get("roster", []) or []
    if not roster:
        print("config.yaml 里 trading_data.roster 为空", file=sys.stderr)
        sys.exit(1)

    n_src = sum(1 for c in roster if c.get("vol_slug") or c.get("fees_slug"))
    print("=== 竞品交易数据 · 抓取 ===")
    print(f"名单 {len(roster)} 家（有数据源 {n_src} 家）\n")
    items = fetch_all(cfg)

    prev = _load_prev(DATA_FILE)
    prev_map = {x["slug"]: x for x in prev.get("items", [])} if prev else {}
    today = today_cst()
    prev_day = prev.get("date")
    for it in items:
        p = prev_map.get(it["slug"])
        d1 = (it.get("vol") or {}).get("d1")
        prev_d1 = (p.get("vol") or {}).get("d1") if p else None
        it["vol_d1_prev"] = prev_d1
        if prev_d1 and d1 is not None and prev_day and prev_day != today:
            it["vol_d1_chg"] = (d1 - prev_d1) / prev_d1
        else:
            it["vol_d1_chg"] = None

    snap = {"date": today, "updated_at": datetime.now(timezone.utc).isoformat(), "items": items}

    users, tier_totals = _fetch_users(cfg)
    snap["users"] = users
    snap["user_tier_totals"] = tier_totals

    pathlib.Path("data").mkdir(exist_ok=True)
    json.dump(snap, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(snap, open("trading_data_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n=== 汇总 ===")
    print(f"交易量 {len(items)} 家 · 用户数 {len(users)} 家；已存 {DATA_FILE}（date={today}）")


if __name__ == "__main__":
    main()
