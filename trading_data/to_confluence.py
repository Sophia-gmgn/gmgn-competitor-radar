#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品交易数据（#6 · stan）→ Confluence。

呈现：核心竞品 / 次要竞品 两张交易量表（各自按 30d 交易量排序），
外加 Dune 活跃用户附表。数据覆盖用状态徽章标注，30d 交易量配占比条。
"""
import os
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_config, now_cst_str
from common.confluence import Confluence, esc, panel, table, status_lozenge

DATA_FILE = os.environ.get("TRADING_DATA_FILE", "data/trading_data.json")


def usd(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:.2f}B"
    if a >= 1e6:
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.1f}K"
    return f"${v:,.0f}"


def num(v):
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def _bar(v, vmax, width=10):
    """30 天交易量占比迷你条（相对本层最大值）。纯文本，Confluence 稳定渲染。"""
    if v is None or not vmax:
        return "—"
    filled = min(width, max(1, round(width * (v / vmax))))
    pct = v / vmax * 100
    return f'{chr(9608)*filled}{chr(9617)*(width-filled)} <sub>{pct:.0f}%</sub>'


def _coverage(row):
    """数据覆盖状态徽章。"""
    if not row["has_vol"]:
        return status_lozenge("暂无数据", "Grey")
    if row.get("via") == "fees":
        return status_lozenge("全链·估算", "Blue")
    if row.get("sol_only"):
        return status_lozenge("仅 Solana", "Yellow")
    return status_lozenge("已收录", "Green")


def _rows_for_tier(roster, tier, items_by_label, users_by_label):
    out = []
    for r in roster:
        if (r.get("tier") or "minor") != tier:
            continue
        label = r.get("label")
        it = items_by_label.get(label) or {}
        v = it.get("vol") or {}
        u = users_by_label.get(label) or {}
        has_vol = bool(v) and (v.get("d30") is not None)
        out.append({
            "label": label, "self": r.get("self"), "note": r.get("note"),
            "sol_only": r.get("sol_only"), "via": it.get("via"), "not_bot": r.get("not_bot"),
            "v1": v.get("d1"), "v7": v.get("d7"), "v14": v.get("d14"), "v30": v.get("d30"),
            "ut": u.get("users_today"), "u7": u.get("users_7d"),
            "u14": u.get("users_14d"), "u30": u.get("users_30d"),
            "chain_note": u.get("chain_note"),
            "has_vol": has_vol, "has_user": bool(u),
        })
    real = sorted([x for x in out if x["has_vol"] and not x.get("not_bot")], key=lambda x: x["v30"] or 0, reverse=True)
    nonbot = sorted([x for x in out if x["has_vol"] and x.get("not_bot")], key=lambda x: x["v30"] or 0, reverse=True)
    novol = [x for x in out if not x["has_vol"]]
    return real + nonbot + novol


def _vol_table(rows):
    vmax = max([x["v30"] for x in rows if x["has_vol"] and not x.get("not_bot")], default=0)
    headers = ["#", "竞品", "数据覆盖", "24h", "7d", "14d", "30d"]
    body = []
    rank = 0
    for i, x in enumerate(rows):
        is_bot = x["has_vol"] and not x.get("not_bot")
        if is_bot:
            rank += 1
        label = esc(x["label"])
        name = f'<strong>⭐ {label}（我们）</strong>' if x["self"] else f"<strong>{label}</strong>"
        if x.get("sol_only"):
            name += ' <sup>*</sup>'
        if x.get("note"):
            name += f' <sub>（{esc(x["note"])}）</sub>'
        body.append([
            str(rank) if is_bot else "—",
            name, _coverage(x),
            usd(x["v1"]), usd(x["v7"]), usd(x["v14"]), usd(x["v30"]),
        ])
    return table(headers, body)


def _user_scope_badge(x):
    """用户口径徽章：全都是全链，仅区分是否含 EVM。"""
    if x.get("chain_note") == "Sol+EVM":
        return status_lozenge("全链 Sol+EVM", "Green")
    return status_lozenge("全链 ≈Sol", "Green")


def _users_tier_table(rows, tier_total):
    """单个层级的活跃用户表：各家全链数 + 末行「层级·全链去重合计」。"""
    urows = [x for x in rows if x["has_user"]]
    if not urows and not tier_total:
        return ""
    urows.sort(key=lambda x: x["u30"] or 0, reverse=True)
    headers = ["竞品", "口径", "活跃用户 当天", "7d", "14d", "30d"]
    body = [[f"<strong>{esc(x['label'])}</strong>", _user_scope_badge(x),
             num(x["ut"]), num(x["u7"]), num(x["u14"]), num(x["u30"])] for x in urows]
    if tier_total:
        body.append([
            "<strong>去重合计</strong>", status_lozenge("全链·跨 bot 去重", "Purple"),
            f'<strong>{num(tier_total.get("users_today"))}</strong>',
            f'<strong>{num(tier_total.get("users_7d"))}</strong>',
            f'<strong>{num(tier_total.get("users_14d"))}</strong>',
            f'<strong>{num(tier_total.get("users_30d"))}</strong>',
        ])
    return table(headers, body)


def render_page(snap):
    items = snap.get("items", [])
    users = snap.get("users", [])
    now = now_cst_str()

    roster = (load_config().get("trading_data", {}) or {}).get("roster") or []
    if not roster:
        roster = [{"label": it["label"], "tier": it.get("tier", "core"),
                   "self": it.get("self"), "sol_only": it.get("sol_only")} for it in items]

    items_by_label = {it.get("label"): it for it in items}
    users_by_label = {u.get("label"): u for u in users}

    core_rows = _rows_for_tier(roster, "core", items_by_label, users_by_label)
    minor_rows = _rows_for_tier(roster, "minor", items_by_label, users_by_label)

    all_bots = sorted([x for x in (core_rows + minor_rows) if x["has_vol"] and not x.get("not_bot")],
                      key=lambda x: x["v30"] or 0, reverse=True)
    self_rank = next((i + 1 for i, x in enumerate(all_bots) if x["self"]), None)
    rank_txt = (f' &nbsp;｜&nbsp; 我们（GMGN）交易量排名 '
                f'<strong>第 {self_rank} / {len(all_bots)}</strong>（全部竞品交易 bot，剔除发币平台 / 聚合器）') if self_rank else ""

    parts = []
    parts.append(panel("info",
        f"<p>📊 <strong>竞品交易数据监控（MEME 交易）</strong>{rank_txt}</p>"
        f"<p><sub>交易量取自 DefiLlama（全链）· 每日自动更新 · 更新于 {esc(now)}（UTC+8）· 请勿手动编辑</sub></p>"))

    parts.append(f"<h2>🥇 核心竞品 · 交易量（重点监控 · {len(core_rows)} 家）</h2>")
    parts.append(_vol_table(core_rows))

    parts.append(f"<h2>📎 次要竞品 · 交易量（泛关注 · {len(minor_rows)} 家）</h2>")
    parts.append(_vol_table(minor_rows))

    tier_totals = snap.get("user_tier_totals", {}) or {}
    core_ut = _users_tier_table(core_rows, tier_totals.get("core"))
    minor_ut = _users_tier_table(minor_rows, tier_totals.get("minor"))
    if core_ut or minor_ut:
        parts.append("<h2>👥 活跃用户数（Dune · 全链口径 · 按独立钱包去重）</h2>")
        parts.append("<p><sub>Solana 部分每日刷新 · EVM 部分每周一刷新 · 「去重合计」为该层竞品钱包合并去重</sub></p>")
        if core_ut:
            parts.append("<h3>🥇 核心竞品</h3>")
            parts.append(core_ut)
        if minor_ut:
            parts.append("<h3>📎 次要竞品</h3>")
            parts.append(minor_ut)

    parts.append(
        "<p><sub><strong>数据说明</strong><br/>"
        "· <strong>交易量</strong>：DefiLlama 全链汇总。"
        "<strong>已收录</strong> = 有全链数据；"
        "<strong>仅 Solana</strong>（带 *）= 该竞品为多链产品但 DefiLlama 仅覆盖其 Solana、数值偏低（如 FOMO）；"
        "<strong>全链·估算</strong> = DefiLlama 无交易量，改用手续费 ÷ 费率估算（如 Maestro，按 1%）；"
        "<strong>暂无数据</strong> = 公开数据源尚未收录，待实际下单反查（Based Bot / DeBot）。<br/>"
        "· <strong>活跃用户</strong>：Dune 链上口径，按独立钱包地址去重。核心竞品（Axiom / Terminal / Trojan / Photon）与 Bloom 的非 Solana 交易占比极低，Solana 数即 ≈ 全链；"
        "<strong>Banana Gun / Maestro</strong> 为 Solana + EVM 合并（Banana Gun 绝大多数用户在 EVM）。"
        "<strong>去重合计</strong> = 该层所有竞品钱包合并去重（同一人用多个 bot 只算一次），故小于各家相加。"
        "Solana 部分每日刷新、EVM 部分每周一刷新；BonkBot / FOMO 暂未纳入用户口径。<br/>"
        "· Pump.fun（发币平台）、Jupiter（DEX 聚合器）口径与交易 bot 不同，仅列数值、不参与排名与占比。<br/>"
        "· Moby、BullX 已移出监控。</sub></p>")
    return "".join(parts)


def main():
    load_dotenv()
    enable_truststore()
    pid = os.environ.get("TRADING_DATA_PAGE_ID", "").strip()
    if not pid:
        print("未设置 TRADING_DATA_PAGE_ID —— 跳过写 Confluence。")
        return
    snap = json.load(open(DATA_FILE, encoding="utf-8"))
    Confluence().update_body(pid, render_page(snap),
                             msg="自动更新 竞品交易数据", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {pid}")


if __name__ == "__main__":
    main()
