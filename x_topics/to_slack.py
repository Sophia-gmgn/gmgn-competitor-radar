#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X 话题日报（#1）→ Slack
=======================
只读 data/x_topics.json，把「当天」话题拼成一条日报，发到同一个竞品监控频道
（用自己的 header「🌐 X 话题日报」和 Cynthia 的账号动态区分开）。
自维护一个极简状态（当天是否已发），避免同日重复播报。

需要：SLACK_BOT_TOKEN + SLACK_CHANNEL（或 SLACK_WEBHOOK_URL）
可选：X_TOPICS_PAGE_URL（页面网址，日报末尾跳转）、X_TOPICS_FORCE_POST=1（测试用，无视当天已发）
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, today_cst
from common.slack import (slack_from_env, post_blocks, header, section, divider,
                          context, link, mrkdwn_escape, pack_units, cap_message_blocks,
                          load_state, save_state)

DATA_FILE = os.environ.get("X_TOPICS_DATA", "data/x_topics.json")
STATE_FILE = os.environ.get("X_TOPICS_SLACK_STATE", "state/x_topics_slack.json")

HEAT_EMOJI = {"高": "🔴", "中": "🟡", "低": "⚪"}
SENT_EMOJI = {"正面": "👍", "负面": "👎", "中性": ""}
HEAT_ORDER = {"高": 0, "中": 1, "低": 2}


def build_digest(day, day_items, page_url):
    blocks = [header(f"🌐 X 话题日报 · {day}")]
    if not day_items:
        blocks.append(section("今日暂无可归纳的讨论话题。"))
    else:
        subs = {}
        for it in day_items:
            subs.setdefault(it.get("subject", "?"), []).append(it)
        blocks.append(section(f"覆盖 *{len(subs)}* 个主体，共 *{len(day_items)}* 个话题。"))
        units = []
        for sub, items in sorted(subs.items(), key=lambda kv: (kv[0] != "GMGN", kv[0])):
            units.append(f"\n*{mrkdwn_escape(sub)}*")
            for it in sorted(items, key=lambda x: HEAT_ORDER.get(x.get("heat"), 3)):
                emo = HEAT_EMOJI.get(it.get("heat"), "⚪")
                se = SENT_EMOJI.get(it.get("sentiment"), "")
                line = f"• {emo}{se} {mrkdwn_escape(it.get('topic'))}"
                ex = it.get("examples") or []
                if ex:
                    line += f"　{link(ex[0], '例')}"
                units.append(line)
        blocks.extend(pack_units(units))
    blocks.append(divider())
    if page_url:
        blocks.append(context(f"完整 / 历史见 Confluence 👉 {link(page_url, 'X 话题日报')}"))
    else:
        blocks.append(context("完整 / 历史见 Confluence「X 话题日报」页"))
    blocks = cap_message_blocks(blocks, page_url)
    return blocks, f"X 话题日报 {day}：{len(day_items)} 个话题"


def main():
    load_dotenv()
    enable_truststore()
    slack = slack_from_env()
    if not slack:
        print("未配置 Slack（SLACK_BOT_TOKEN + SLACK_CHANNEL 或 SLACK_WEBHOOK_URL）—— 跳过 Slack 推送。"
              "（Confluence 输出不受影响；等要上 Slack 时再配这两个即可。）")
        return

    page_url = os.environ.get("X_TOPICS_PAGE_URL", "").strip()
    force = os.environ.get("X_TOPICS_FORCE_POST", "0") == "1"

    store = load_store(DATA_FILE)
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    # 优先发「今天」；今天没有就发最近一天（便于手动补发）
    today = today_cst()
    target = today if today in dates else (dates[0] if dates else today)
    day_items = [it for it in store if it.get("date") == target]

    state = load_state(STATE_FILE)
    if not force and state.get("last_posted") == target:
        print(f"跳过：{target} 已发过（如需重发设 X_TOPICS_FORCE_POST=1）")
        return

    blocks, fallback = build_digest(target, day_items, page_url)
    if post_blocks(slack, blocks, fallback):
        if not force:
            state["last_posted"] = target
            save_state(STATE_FILE, state)
        print(f"✓ 已发送 X 话题日报（{target}，{len(day_items)} 个话题）")
    else:
        print("发送失败", file=sys.stderr)


if __name__ == "__main__":
    main()
