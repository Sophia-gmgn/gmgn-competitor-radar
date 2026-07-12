#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品交易数据（#6 · stan）→ Confluence（统一一张表：交易量 + 用户数）。"""
import os
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_config, now_cst_str
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


def num(v):
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def render_page(snap):
    items = snap.get("items", [])
    users = snap.get("users", [])
    now = now_cst_str()

    roster = (load_config().get("trading_data", {}) or {}).get("roster") or []
    if not roster:
        roster = [{"label": it["label"], "vol_slug": it["slug"], "self": it.get("self")}
                  for it in items]
        have = {r["label"] for r in roster}
        for u in users:
            if u["label"] not in have:
                roster.append({"label": u["label"]})

    vol_by_slug = {it.get("slug"): it for it in items}
    user_by_label = {u.get("label"): u for u in users}

    rows_data = []
    for r in roster:
        label = r.get("label")
        vol = vol_by_slug.get(r.get("vol_slug")) if r.get("vol_slug") else None
        v = (vol or {}).get("vol") or {}
        u = user_by_label.get(label) or {}

        has_llama = bool(v)
        vol_sol = (not has_llama) and (u.get("dvol_30d") is not None)
        v1  = v.get("d1")  if has_llama else (u.get("dvol_1d")  if vol_sol else None)
        v7  = v.get("d7")  if has_llama else (u.get("dvol_7d")  if vol_sol else None)
        v14 = v.get("d14") if has_llama else (u.get("dvol_14d") if vol_sol else None)
        v30 = v.get("d30") if has_llama else (u.get("dvol_30d") if vol_sol else None)

        rows_data.append({
            "label": label, "self": r.get("self"), "vol_sol": vol_sol,
            "v1": v1, "v7": v7, "v14": v14, "v30": v30,
            "ut": u.get("users_today"), "u7": u.get("users_7d"),
            "u14": u.get("users_14d"), "u30": u.get("users_30d"),
            "has_vol": (v30 is not None), "has_user": bool(u),
        })

    g1 = sorted([x for x in rows_data if x["has_vol"]], key=lambda x: x["v30"] or 0, reverse=True)
    g2 = sorted([x for x in rows_data if not x["has_vol"] and x["has_user"]],
                key=lambda x: x["u30"] or 0, reverse=True)
    g3 = [x for x in rows_data if not x["has_vol"] and not x["has_user"]]
    ordered = g1 + g2 + g3

    self_rank = next((i + 1 for i, x in enumerate(g1) if x["self"]), None)
    n_vol = len(g1)
    rank_txt = f"我们（GMGN）交易量排名 <strong>第 {self_rank} / {n_vol}</strong>" if self_rank else ""

    parts = [panel("info",
                   f"<p>📊 竞品交易数据监控（MEME 交易）· 共 <strong>{len(ordered)}</strong> 家 &nbsp;｜&nbsp; {rank_txt}</p>"
                   f"<p><sub>交易量取自 DefiLlama（全链）· 活跃用户取自 Dune（仅 Solana，按独立钱包去重）· 每日更新 · 更新于 {esc(now)}（UTC+8）· 请勿手动编辑</sub></p>")]

    headers = ["竞品", "交易量 24h", "交易量 7d", "交易量 14d", "交易量 30d",
               "用户 当天", "用户 7d", "用户 14d", "用户 30d"]
    rows = []
    for i, x in enumerate(ordered):
        label = esc(x["label"])
        name = f'<strong>⭐ {label}（我们）</strong>' if x["self"] else label
        if x.get("vol_sol"):
            name = f'{name} <sup>*</sup>'
        name = f'{i+1}. {name}'
        rows.append([
            name,
            usd(x["v1"]), usd(x["v7"]), usd(x["v14"]), usd(x["v30"]),
            num(x["ut"]), num(x["u7"]), num(x["u14"]), num(x["u30"]),
        ])
    parts.append(table(headers, rows))

    parts.append('<p><sub>说明：<strong>交易量</strong> = 全链汇总（DefiLlama）；<strong>用户数</strong> = 仅 Solana 链、当天按北京时间 0 点起算、按独立钱包地址去重（Dune），多链竞品（如 Banana Gun / Maestro）的非 Solana 用户未计入，故偏低。<br/>'
                 '带 <sup>*</sup> 的交易量为仅 Solana 口径（Dune，因该竞品未被 DefiLlama 收录，无全链数据）。DeBot / BasedBot / Moby 两个数据源暂无收录（“—”）；Photon / GMGN / Axiom 暂无 Solana 用户标签（“—”）；BullX 已排除。后续用 Dune 自建查询逐步补齐。</sub></p>')
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
