#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品功能更新 → Confluence（按竞品分组 + 卡片式）。"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, now_cst_str
from common.confluence import Confluence, esc, panel, status_lozenge

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
MAX_ITEMS_PER_COMP = int(os.environ.get("FEATURE_UPDATES_MAX_PER_COMP", "40"))

TYPE_COLOUR = {"功能更新": "Green", "集成": "Purple", "活动": "Yellow", "公告": "Blue", "其它": None}
IMPORTANT = {"功能更新", "集成"}
TYPE_EMOJI = {"功能更新": "🆕", "集成": "🔗", "活动": "🎁", "公告": "📢", "其它": "📝"}


def _src_link(it):
    return f' &nbsp;·&nbsp; <a href="{esc(it.get("url"))}">🔗 原文</a>' if it.get("url") else ""


def render_item(it):
    typ = it.get("type", "其它")
    tag = status_lozenge(typ, TYPE_COLOUR.get(typ))
    title = esc(it.get("title", ""))
    summary = esc(it.get("summary", ""))
    date = esc(it.get("date", ""))
    src = _src_link(it)
    if typ in IMPORTANT:
        inner = (f'<p>{tag} &nbsp;<sub>{date}</sub></p>'
                 f'<p><strong>{title}</strong></p>'
                 f'<p>{summary}{src}</p>')
        return panel("tip", inner)
    return (f'<p>{tag} &nbsp;<strong>{title}</strong> &nbsp;<sub>{date}</sub></p>'
            f'<p>{summary}{src}</p>')


def render_page(store):
    now = now_cst_str()
    total = len(store)

    comps = {}
    for it in store:
        comps.setdefault(it.get("competitor", "?"), []).append(it)

    def recent(items):
        return max((i.get("date", "") for i in items), default="")

    ordered = sorted(comps.items(), key=lambda kv: recent(kv[1]), reverse=True)

    if ordered:
        counts = " &nbsp;·&nbsp; ".join(f"<strong>{esc(c)}</strong> {len(items)}"
                                        for c, items in ordered)
    else:
        counts = "暂无"
    parts = [panel("info",
                   f"<p>📊 竞品功能更新监控 · 共 <strong>{total}</strong> 条 &nbsp;｜&nbsp; {counts}</p>"
                   f"<p><sub>本页由脚本自动更新，更新于 {esc(now)}（UTC+8）· 请勿手动编辑（每次整页重写）</sub></p>")]

    if not store:
        parts.append(panel("note", "<p>暂无功能更新（等首次抓取写入）。</p>"))
        return "".join(parts)

    for comp, items in ordered:
        items = sorted(items, key=lambda x: x.get("date", ""), reverse=True)[:MAX_ITEMS_PER_COMP]
        n_imp = sum(1 for x in items if x.get("type") in IMPORTANT)
        badge = f'　<sub>{len(items)} 条' + (f'，{n_imp} 条功能更新' if n_imp else '') + '</sub>'
        parts.append(f'<h2>🔹 {esc(comp)}{badge}</h2>')
        for it in items:
            parts.append(render_item(it))
    return "".join(parts)


def main():
    load_dotenv()
    enable_truststore()
    pid = os.environ.get("FEATURE_UPDATES_PAGE_ID", "").strip()
    if not pid:
        print("未设置 FEATURE_UPDATES_PAGE_ID —— 跳过写 Confluence。")
        return
    store = load_store(DATA_FILE)
    Confluence().update_body(pid, render_page(store),
                             msg="自动更新 竞品功能更新", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {pid}（{len(store)} 条，按竞品分组）")


if __name__ == "__main__":
    main()
