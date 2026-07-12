#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品交易数据（#6 · stan）→ Confluence（仅交易量）。"""
import os
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, now_cst_str
from common.confluence import Confluence, esc, panel, table

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


def chg_html(v):
    if v is None:
        return '<span style="color:#6B778C">—</span>'
    if v > 0:
        return f'<span style="color:#0B875B">▲ {v*100:.1f}%</span>'
    if v < 0:
        return f'<span style="color:#DE350B">▼ {abs(v)*100:.1f}%</span>'
    return '<span style="color:#6B778C">0%</span>'


def name_cell(it, rank):
    label = esc(it["label"])
    if it.get("self"):
        return f'{rank}. <strong>⭐ {label}（我们）</strong>'
    return f'{rank}. {label}'


def render_page(snap):
    items = snap.get("items", [])
    now = now_cst_str()

    def vkey(it):
        return (it.get("vol") or {}).get("d30") or 0
    ranked = sorted(items, key=vkey, reverse=True)

    self_rank = next((i + 1 for i, it in enumerate(ranked) if it.get("self")), None)
    n = len(ranked)
    rank_txt = f"我们（GMGN）交易量排名 <strong>第 {self_rank} / {n}</strong>" if self_rank else ""

    parts = [panel("info",
                   f"<p>📊 竞品交易数据监控（MEME 交易 · 交易量）· 共 <strong>{n}</strong> 家 &nbsp;｜&nbsp; {rank_txt}</p>"
                   f"<p><sub>数据源 DefiLlama · 每日更新 · 更新于 {esc(now)}（UTC+8）· 请勿手动编辑（每次整页重写）</sub></p>")]

    if not items:
        parts.append(panel("note", "<p>暂无数据。</p>"))
        return "".join(parts)

    parts.append("<h2>💵 交易量排行</h2>")
    headers = ["排名 / 竞品", "24h", "7d", "14d", "30d", "较昨日(24h)"]
    rows = []
    for i, it in enumerate(ranked):
        v = it.get("vol") or {}
        rows.append([
            name_cell(it, i + 1),
            usd(v.get("d1")), usd(v.get("d7")), usd(v.get("d14")), usd(v.get("d30")),
            chg_html(it.get("vol_d1_chg")),
        ])
    parts.append(table(headers, rows))
    parts.append('<p><sub>说明：交易量取自 DefiLlama。DeBot / Maestro / Terminal / Moby 暂未纳入（DefiLlama 未收录，后续用 Dune 补，并补充「用户数」维度）。BullX 已排除。</sub></p>')
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
    print(f"✓ 已写入 Confluence 页 {pid}（{len(snap.get('items',[]))} 家）")


if __name__ == "__main__":
    main()
