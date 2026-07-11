#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞品功能更新 → Slack（暂不接；未配置则自动跳过）
================================================
读 data/feature_updates.json，把「当天」的功能更新拼成一条日报发到监控频道
（header「🛠 竞品功能更新」，与功能①、Cynthia 账号动态区分开）。同日去重。

需要：SLACK_BOT_TOKEN + SLACK_CHANNEL（或 SLACK_WEBHOOK_URL）。没配就跳过。
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, today_cst
from common.slack import (slack_from_env, post_blocks, header, section, divider,
                          context, link, mrkdwn_escape, pack_units, cap_message_blocks,
                          load_state, save_state)

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
STATE_FILE = os.environ.get("FEATURE_UPDATES_SLACK_STATE", "state/feature_updates_slack.json")

TYPE_EMOJI = {"新功能": "🆕", "优化": "⚙️", "集成": "🔗", "其它": "📣"}


def build_digest(day, items, page_url):
    blocks = [header(f"🛠 竞品功能更新 · {day}")]
    if not items:
        blocks.append(section("今日无新功能更新。"))
    else:
        blocks.append(section(f"共 *{len(items)}* 条。"))
        units = []
        for it in items:
            emo = TYPE_EMOJI.get(it.get("type"), "📣")
            line = f"{emo} *{mrkdwn_escape(it.get('competitor'))}*：{mrkdwn_escape(it.get('title'))}"
            if it.get("url"):
                line += f"　{link(it['url'], '原文')}"
            units.append(line)
        blocks.extend(pack_units(units))
    blocks.append(divider())
    blocks.append(context(
        f"完整 / 历史见 Confluence 👉 {link(page_url, '竞品功能更新')}"
        if page_url else "完整 / 历史见 Confluence「竞品功能更新」页"))
    return cap_message_blocks(blocks, page_url), f"竞品功能更新 {day}：{len(items)} 条"


def main():
    load_dotenv()
    enable_truststore()
    slack = slack_from_env()
    if not slack:
        print("未配置 Slack（SLACK_BOT_TOKEN + SLACK_CHANNEL 或 SLACK_WEBHOOK_URL）—— "
              "跳过 Slack 推送。（Confluence 输出不受影响。）")
        return

    store = load_store(DATA_FILE)
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    today = today_cst()
    target = today if today in dates else (dates[0] if dates else today)
    items = [it for it in store if it.get("date") == target]

    force = os.environ.get("FEATURE_UPDATES_FORCE_POST", "0") == "1"
    st = load_state(STATE_FILE)
    if not force and st.get("last_posted") == target:
        print(f"跳过：{target} 已发过（如需重发设 FEATURE_UPDATES_FORCE_POST=1）")
        return

    page_url = os.environ.get("FEATURE_UPDATES_PAGE_URL", "").strip()
    blocks, fb = build_digest(target, items, page_url)
    if post_blocks(slack, blocks, fb):
        if not force:
            st["last_posted"] = target
            save_state(STATE_FILE, st)
        print(f"✓ 已发送 竞品功能更新（{target}，{len(items)} 条）")


if __name__ == "__main__":
    main()
