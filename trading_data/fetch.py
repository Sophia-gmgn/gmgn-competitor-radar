#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品交易数据（#6 · stan）—— 抓取（DefiLlama 交易量 + Dune 用户数）。"""
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
                "name": d.get("name")}
    except Exception:
        return None


def fetch_all(cfg):
    comps = cfg.get("competitors", []) or []
    out = []
    with httpx.Client(headers={"User-Agent": "gmgn-radar/1.0"}) as client:
        for c in comps:
            slug = str(c.get("slug") or "").strip()
            label = c.get("label") or slug
            is_self = bool(c.get("self"))
            if not slug:
                continue
            vol = _fetch_volume(client, slug)
            if not vol:
                print(f"  [{label}] ❌ 无交易量数据（slug={slug}）")
                continue
            out.append({"label": label, "slug": slug, "self": is_self, "vol": vol})
            v30 = vol.get("d30") or 0
            v1 = vol.get("d1") or 0
            print(f"  [{label}] 24h=${v1:,.0f}  30d=${v30:,.0f}")
    return out


def _load_prev(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def main():
    load_dotenv()
    enable_truststore()
    cfg = load_config().get("trading_data", {}) or {}
    comps = cfg.get("competitors", []) or []
    if not comps:
        print("config.yaml 里 trading_data.competitors 为空", file=sys.stderr)
        sys.exit(1)

    print("=== 竞品交易数据 · 抓取 ===")
    print(f"竞品 {len(comps)} 家\n")
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

    # 用户数（Dune dex_solana.bot_trades；失败不影响交易量）
    qid = cfg.get("dune_users_query_id")
    bot_map = cfg.get("dune_bot_map") or {}
    users = []
    if qid and os.environ.get("DUNE_API_KEY", "").strip():
        try:
            from common.dune import get_query_result
            rows = get_query_result(int(qid))
            for r in rows:
                raw = str(r.get("bot", "")).strip()
                label = bot_map.get(raw, raw)
                users.append({
                    "label": label,
                    "users_today": r.get("users_today"),
                    "users_7d": r.get("users_7d"),
                    "users_14d": r.get("users_14d"),
                    "users_30d": r.get("users_30d"),
                    "dvol_1d": r.get("vol_1d"),
                    "dvol_7d": r.get("vol_7d"),
                    "dvol_14d": r.get("vol_14d"),
                    "dvol_30d": r.get("vol_30d"),
                })
            users.sort(key=lambda x: x.get("users_7d") or 0, reverse=True)
            print(f"\n用户数（Dune）：{len(users)} 家")
            for u in users:
                print(f"  [{u['label']}] 7d活跃={u.get('users_7d')}  30d活跃={u.get('users_30d')}")
        except Exception as e:
            print(f"\n⚠️ Dune 用户数抓取失败（不影响交易量）：{e}")
    else:
        print("\n（未配置 DUNE_API_KEY 或 query id，跳过用户数）")
    snap["users"] = users

    pathlib.Path("data").mkdir(exist_ok=True)
    json.dump(snap, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(snap, open("trading_data_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n=== 汇总 ===")
    print(f"交易量 {len(items)} 家 · 用户数 {len(users)} 家；已存 {DATA_FILE}（date={today}）")


if __name__ == "__main__":
    main()
