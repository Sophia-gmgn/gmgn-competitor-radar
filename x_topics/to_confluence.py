#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X 话题日报（#1）→ Confluence
============================
只读 data/x_topics.json，渲染成「X 话题日报」页（按日期倒序，每天分主体列话题）。
按固定 pageId 覆盖写、保留你写好的页面标题。

需要：ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN / X_TOPICS_PAGE_ID
（未配 X_TOPICS_PAGE_ID 时安静跳过，方便你还没建页时先只跑抓取。）
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, now_cst_str
from common.confluence import Confluence, esc, panel, status_lozenge, expand

DATA_FILE = os.environ.get("X_TOPICS_DATA", "data/x_topics.json")
MAX_DAYS = int(os.environ.get("X_TOPICS_PAGE_DAYS", "14"))   # 页面最多展示最近多少天

HEAT_COLOUR = {"高": "Red", "中": "Yellow", "低": None}
SENT_COLOUR = {"正面": "Green", "负面": "Red", "中性": None}
HEAT_ORDER = {"高": 0, "中": 1, "低": 2}


def render_day(day_items):
    subs = {}
    for it in day_items:
        subs.setdefault(it.get("subject", "?"), []).append(it)
    out = []
    # GMGN（自家）排前，其余按名称
    for sub, items in sorted(subs.items(), key=lambda kv: (kv[0] != "GMGN", kv[0])):
        out.append(f"<h3>{esc(sub)}（{len(items)}）</h3>")
        lis = []
        for it in sorted(items, key=lambda x: HEAT_ORDER.get(x.get("heat"), 3)):
            heat = status_lozenge(f"热度{it.get('heat')}", HEAT_COLOUR.get(it.get("heat")))
            sent = status_lozenge(it.get("sentiment") or "中性", SENT_COLOUR.get(it.get("sentiment")))
            ex = " · ".join(
                f'<a href="{esc(u)}">例{j + 1}</a>'
                for j, u in enumerate((it.get("examples") or [])[:3]) if u)
            tail = f"　{ex}" if ex else ""
            lis.append(f"<li><p>{heat} {sent} {esc(it.get('topic'))}{tail}</p></li>")
        out.append("<ul>" + "".join(lis) + "</ul>")
    return "".join(out)


def render_page(store):
    parts = [panel("info",
                   f"<p>本页由脚本自动更新（每日 4 次）。更新于 {esc(now_cst_str())}（UTC+8）。"
                   f"<strong>请勿手动编辑</strong>（每次整页重写，手动改动会被覆盖）。</p>")]
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    if not dates:
        parts.append(panel("note", "<p>暂无数据（等首次抓取写入）。</p>"))
        return "".join(parts)
    for i, d in enumerate(dates[:MAX_DAYS]):
        day_items = [it for it in store if it.get("date") == d]
        body = render_day(day_items)
        if i == 0:
            parts.append(f"<h2>{esc(d)}（最新）</h2>")
            parts.append(body)
        else:
            parts.append(expand(f"{d}（{len(day_items)} 个话题）", body))
    return "".join(parts)


def main():
    load_dotenv()
    enable_truststore()
    page_id = os.environ.get("X_TOPICS_PAGE_ID", "").strip()
    if not page_id:
        print("未设置 X_TOPICS_PAGE_ID —— 跳过写 Confluence（先建好「X 话题日报」页、把 pageId 填进来）。")
        return

    store = load_store(DATA_FILE)
    storage = render_page(store)
    cf = Confluence()
    cf.update_body(page_id, storage, msg="自动更新 X 话题日报", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {page_id}（{len(store)} 条底稿，展示最近 {MAX_DAYS} 天）")


if __name__ == "__main__":
    main()
