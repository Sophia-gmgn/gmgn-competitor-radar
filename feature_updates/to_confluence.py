#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞品功能更新（#2 + #4 + #3）→ Confluence
=========================================
读 data/feature_updates.json，按日期倒序渲染成「竞品功能更新」页，每条一张卡片
（竞品 + 类型标签 + 标题 + 摘要 + 原文链接）。按固定 pageId 覆盖写、保留页面标题。

需要：ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN / FEATURE_UPDATES_PAGE_ID
（未配 pageId 时安静跳过。）
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, now_cst_str
from common.confluence import Confluence, esc, panel, status_lozenge, expand

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
MAX_DAYS = int(os.environ.get("FEATURE_UPDATES_PAGE_DAYS", "30"))

TYPE_COLOUR = {"新功能": "Green", "优化": "Blue", "集成": "Purple", "其它": None}


def render_item(it):
    tag = status_lozenge(it.get("type", "其它"), TYPE_COLOUR.get(it.get("type")))
    comp = f'<strong>{esc(it.get("competitor"))}</strong>'
    src = f' · <a href="{esc(it.get("url"))}">原文</a>' if it.get("url") else ""
    summary = f'<p>{esc(it.get("summary"))}</p>' if it.get("summary") else ""
    return (f'<p>{tag} {comp}：{esc(it.get("title"))}{src}</p>{summary}')


def render_day(day_items):
    return "".join(f'<hr/>{render_item(it)}' for it in day_items)


def render_page(store):
    parts = [panel("info",
                   f"<p>本页由脚本自动更新。更新于 {esc(now_cst_str())}（UTC+8）。"
                   f"<strong>请勿手动编辑</strong>（每次整页重写）。</p>")]
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    if not dates:
        parts.append(panel("note", "<p>暂无功能更新（等首次抓取写入）。</p>"))
        return "".join(parts)
    for i, d in enumerate(dates[:MAX_DAYS]):
        day = [it for it in store if it.get("date") == d]
        body = render_day(day)
        if i == 0:
            parts.append(f"<h2>{esc(d)}（最新）</h2>")
            parts.append(body)
        else:
            parts.append(expand(f"{d}（{len(day)} 条）", body))
    return "".join(parts)


def main():
    load_dotenv()
    enable_truststore()
    pid = os.environ.get("FEATURE_UPDATES_PAGE_ID", "").strip()
    if not pid:
        print("未设置 FEATURE_UPDATES_PAGE_ID —— 跳过写 Confluence"
              "（把「竞品功能更新」页的 pageId 填进来即可）。")
        return
    store = load_store(DATA_FILE)
    Confluence().update_body(pid, render_page(store),
                             msg="自动更新 竞品功能更新", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {pid}（{len(store)} 条，展示最近 {MAX_DAYS} 天）")


if __name__ == "__main__":
    main()
