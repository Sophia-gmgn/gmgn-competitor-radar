# -*- coding: utf-8 -*-
"""
Slack 封装 —— 改自 Cynthia 的 grok_slack。
Block Kit 构造 + 发送（Bot Token 优先，回退 Incoming Webhook）+ 轻量状态读写。
"""
import os
import sys
import json
import pathlib

import httpx

SLACK_API = "https://slack.com/api/chat.postMessage"
SECTION_CHAR_BUDGET = 2900   # 单 block 文本安全上限（Slack 硬限 ~3000）
MAX_MSG_BLOCKS = 48          # 单条消息 block 数安全上限（Slack 硬限 ~50）


def mrkdwn_escape(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def link(url, text):
    url = (url or "").strip()
    return f"<{url}|{mrkdwn_escape(text)}>" if url else mrkdwn_escape(text)


def section(md):
    return {"type": "section", "text": {"type": "mrkdwn", "text": md}}


def header(txt):
    return {"type": "header", "text": {"type": "plain_text", "text": txt[:150], "emoji": True}}


def divider():
    return {"type": "divider"}


def context(md):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": md}]}


def pack_units(units, budget=SECTION_CHAR_BUDGET):
    """把若干文本单元打包成多个 section block（每块 ≤ budget），单元本身不拆开。"""
    blocks, buf, cur = [], [], 0
    for u in units:
        add = len(u) + (1 if buf else 0)
        if buf and cur + add > budget:
            blocks.append(section("\n".join(buf).strip("\n")))
            buf, cur = [], 0
            add = len(u)
        buf.append(u)
        cur += add
    if buf:
        blocks.append(section("\n".join(buf).strip("\n")))
    return blocks


def cap_message_blocks(blocks, tail_url=None, max_blocks=MAX_MSG_BLOCKS):
    if len(blocks) <= max_blocks:
        return blocks
    kept = blocks[:max_blocks - 1]
    tail = (f"　…余下内容超出单条消息容量，详见 {link(tail_url, 'Confluence')}"
            if tail_url else "　…余下内容超出单条消息容量，详见 Confluence")
    kept.append(context(tail))
    return kept


def slack_from_env():
    """优先 Bot Token（xoxb-）+ channel；否则 Incoming Webhook；都没有返回 None。"""
    bot = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    ch = os.environ.get("SLACK_CHANNEL", "").strip()
    hook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if bot and ch:
        return {"mode": "api", "token": bot, "channel": ch, "webhook": ""}
    if hook:
        return {"mode": "webhook", "token": "", "channel": "", "webhook": hook}
    return None


def post_blocks(slack, blocks, fallback):
    """发一条 Block Kit 消息。成功返回 True。"""
    try:
        if slack["mode"] == "api":
            r = httpx.post(
                SLACK_API,
                headers={"Authorization": f"Bearer {slack['token']}",
                         "Content-Type": "application/json; charset=utf-8"},
                json={"channel": slack["channel"], "text": fallback, "blocks": blocks, "unfurl_links": False, "unfurl_media": False},
                timeout=30,
            )
            ok = r.status_code == 200 and r.json().get("ok")
            if not ok:
                print(f"[slack] 发送失败：{r.status_code} {r.text[:200]}", file=sys.stderr)
            return bool(ok)
        else:
            r = httpx.post(slack["webhook"], json={"text": fallback, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}, timeout=30)
            ok = r.status_code == 200
            if not ok:
                print(f"[slack] webhook 失败：{r.status_code} {r.text[:200]}", file=sys.stderr)
            return ok
    except Exception as e:
        print(f"[slack] 异常：{e}", file=sys.stderr)
        return False


def load_state(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path, state):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
