#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X 话题日报（#1）→ Confluence（核心区详细 / 次要区简略）"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, load_config, now_cst_str
from common.confluence import Confluence, esc, panel, status_lozenge, expand

DATA_FILE = os.environ.get("X_TOPICS_DATA", "data/x_topics.json")
MAX_DAYS = int(os.environ.get("X_TOPICS_PAGE_DAYS", "14"))

HEAT_COLOUR = {"高": "Red", "中": "Yellow", "低": None}
SENT_COLOUR = {"正面": "Green", "负面": "Red", "中性": None}
HEAT_ORDER = {"高": 0, "中": 1, "低": 2}

MINOR_TOPICS = 2


def _tier_maps():
    subs = (load_config().get("x_topics", {}) or {}).get("subjects", []) or []
    tier = {}
    order = {}
    for i, s in enumerate(subs):
        label = s.get("label", s.get("key", ""))
        tier[label] = s.get("tier", "core")
        order[label] = i
    return tier, order


def _topic_li(it, detailed=True):
    heat = status_lozenge(f"热度{it.get('heat')}", HEAT_COLOUR.get(it.get("heat")))
    sent = status_lozenge(it.get("sentiment") or "中性", SENT_COLOUR.get(it.get("sentiment")))
    if detailed:
        ex = " · ".join(
            f'<a href="{esc(u)}">例{j + 1}</a>'
            for j, u in enumerate((it.get("examples") or [])[:3]) if u)
        tail = f"　{ex}" if ex else ""
        return f"<li><p>{heat} {sent} {esc(it.get('topic'))}{tail}</p></li>"
    else:
        ex0 = (it.get("examples") or [])
        link = f'　<a href="{esc(ex0[0])}">例</a>' if ex0 and ex0[0] else ""
        return f"<li><p>{heat} {esc(it.get('topic'))}{link}</p></li>"


def render_day(day_items, tier, order):
    subs = {}
    for it in day_items:
        subs.setdefault(it.get("subject", "?"), []).append(it)

    core_subs = {k: v for k, v in subs.items() if tier.get(k, "core") == "core"}
    minor_subs = {k: v for k, v in subs.items() if tier.get(k, "core") == "minor"}

    out = []

    if core_subs:
        out.append('<h3>🎯 核心竞品</h3>')
        for sub, items in sorted(core_subs.items(),
                                 key=lambda kv: (kv[0] != "GMGN", order.get(kv[0], 99))):
            out.append(f"<h4>{esc(sub)}（{len(items)}）</h4>")
            lis = [_topic_li(it, detailed=True)
                   for it in sorted(items, key=lambda x: HEAT_ORDER.get(x.get("heat"), 3))]
            out.append("<ul>" + "".join(lis) + "</ul>")

    if minor_subs:
        out.append('<h3>📎 次要竞品（简况）</h3>')
        rows = []
        for sub, items in sorted(minor_subs.items(), key=lambda kv: order.get(kv[0], 99)):
            top = sorted(items, key=lambda x: HEAT_ORDER.get(x.get("heat"), 3))[:MINOR_TOPICS]
            lis = "".join(_topic_li(it, detailed=False) for it in top)
            rows.append(f"<p><strong>{esc(sub)}</strong></p><ul>{lis}</ul>")
        out.append("".join(rows))

    return "".join(out)


def render_page(store):
    tier, order = _tier_maps()
    parts = [panel("info",
                   f"<p>本页由脚本自动更新（每日 4 次）。更新于 {esc(now_cst_str())}（UTC+8）。"
                   f"<strong>请勿手动编辑</strong>（每次整页重写）。</p>"
                   f"<p><sub>🎯 核心竞品详细展示；📎 次要竞品仅列简况（每家至多 {MINOR_TOPICS} 条热门话题）。</sub></p>")]
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    if not dates:
        parts.append(panel("note", "<p>暂无数据（等首次抓取写入）。</p>"))
        return "".join(parts)
    for i, d in enumerate(dates[:MAX_DAYS]):
        day_items = [it for it in store if it.get("date") == d]
        body = render_day(day_items, tier, order)
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
        print("未设置 X_TOPICS_PAGE_ID —— 跳过写 Confluence。")
        return
    store = load_store(DATA_FILE)
    storage = render_page(store)
    cf = Confluence()
    cf.update_body(page_id, storage, msg="自动更新 X 话题日报", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {page_id}（{len(store)} 条，最近 {MAX_DAYS} 天）")


if __name__ == "__main__":
    main()
