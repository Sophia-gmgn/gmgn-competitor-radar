#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品功能更新 → Slack（读 store · 只推核心竞品 · 每竞品≤3 · 带研判 · 逐条去重）"""
import os, re, sys, pathlib
from datetime import datetime, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common.util import (load_dotenv, enable_truststore, load_store, load_config, today_cst, CST)
from common.slack import (slack_from_env, post_blocks, header, section, divider,
                          context, link, mrkdwn_escape, pack_units, cap_message_blocks,
                          load_state, save_state)

DATA_FILE   = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
LEDGER_FILE = os.environ.get("PUSH_LEDGER", "state/push_ledger.json")
PAGE_URL    = os.environ.get("FEATURE_UPDATES_PAGE_URL",
    "https://pionex.atlassian.net/wiki/spaces/~712020158205a1add2439fae73253b196308bc/pages/2303656153")
LEDGER_KEEP_DAYS = 3
MAX_PER_COMP = 3

def _norm_name(s):
    s = (s or "").lower().strip()
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)
    return re.sub(r"[\s\u3000._\-]", "", s)

def _core_set():
    fu = load_config().get("feature_updates", {}) or {}
    return {_norm_name(d.get("label", "")) for d in (fu.get("directory", []) or []) if d.get("tier") == "core"}

def _is_bad(it):
    # 坏数据：社群页列错位导致——正文(summary)只是颜色/优先级标记，或标题/正文异常短
    s = (it.get("summary") or "").strip()
    t = (it.get("title") or "").strip()
    if re.match(r"^(Yellow|Red|Grey|Green|Blue|Purple)\s*[高中低]?$", s):
        return True
    if re.match(r"^(Yellow|Red|Grey|Green|Blue|Purple)\s*[高中低]?$", t):
        return True
    if len(s) < 4:  # 正文过短（如空、"中"、"高"），基本是错位残留
        return True
    return False

def _is_webjs(it):
    u = str(it.get("url", "")); return u.endswith(".js") or "/assets/" in u

def _prune_ledger(led):
    cutoff = (datetime.now(CST) - timedelta(days=LEDGER_KEEP_DAYS)).strftime("%Y-%m-%d")
    led["posted"] = {k: v for k, v in (led.get("posted", {}) or {}).items() if v >= cutoff}
    return led

def _comp_block(comp, items):
    """一家竞品 = 一个 section block（标题+类型标签，正文=每条：标题/研判）。"""
    lines = []
    for it in items[:MAX_PER_COMP]:
        typ = it.get("type", "其它")
        head = f"*{mrkdwn_escape(comp)}*　`{mrkdwn_escape(typ)}`"
        title = f"{mrkdwn_escape(it.get('title',''))}"
        body = mrkdwn_escape(it.get("summary", "")) if it.get("summary") else ""
        piece = f"{head}\n{title}"
        if body:
            piece += f"\n{body}"
        if it.get("url"):
            piece += f"　{link(it['url'], '原文')}"
        lines.append(piece)
    if len(items) > MAX_PER_COMP:
        lines.append(f"_↳ {mrkdwn_escape(comp)} 还有 {len(items) - MAX_PER_COMP} 条 → {link(PAGE_URL, '看板')}_")
    return "\n\n".join(lines)

def build_blocks(day, comp_items):
    total = sum(len(v) for v in comp_items.values())
    blocks = [section(f"📢 *竞品功能更新* · {day} · 核心 {total} 条"), divider()]
    units = [_comp_block(c, its) for c, its in comp_items.items()]
    blocks.extend(pack_units(units))
    blocks.append(divider())
    blocks.append(section(f"📋 *更多功能更新，点这里* 👉 {link(PAGE_URL, '竞品功能更新看板')}"))
    return cap_message_blocks(blocks, PAGE_URL), f"竞品功能更新 {day}：{total} 条"

def main():
    load_dotenv(); enable_truststore()
    dry = os.environ.get("FEATURE_UPDATES_DRY_RUN", "0") == "1"
    slack = slack_from_env()
    if not slack and not dry:
        print("未配置 Slack（SLACK_BOT_TOKEN + SLACK_CHANNEL）—— 跳过。"); return
    core, store = _core_set(), load_store(DATA_FILE)
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    target = os.environ.get("FEATURE_UPDATES_TARGET_DATE", "").strip() or today_cst()
    if target not in dates: target = dates[0] if dates else target
    todays = [it for it in store if it.get("date") == target
              and _norm_name(it.get("competitor", "")) in core
              and not _is_bad(it) and not _is_webjs(it)]
    led = _prune_ledger(load_state(LEDGER_FILE)); posted = led.get("posted", {})
    fresh = [it for it in todays if it.get("_id") and it["_id"] not in posted]
    if not fresh:
        print(f"[{target}] 核心竞品无新增高优功能更新（{len(todays)} 条今日均已推）—— 不发送。"); return
    from collections import OrderedDict
    grouped = OrderedDict()
    for it in fresh: grouped.setdefault(it.get("competitor"), []).append(it)
    blocks, fb = build_blocks(target, grouped)
    if dry:
        print("===== DRY_RUN（不发送、不写台账）=====\nfallback:", fb, "\n")
        for c, its in grouped.items():
            print(_comp_block(c, its).replace("*", "").replace("`", ""), "\n")
        return
    if post_blocks(slack, blocks, fb):
        for it in fresh: posted[it["_id"]] = target
        led["posted"] = posted; save_state(LEDGER_FILE, led)
        print(f"✓ 已发送 竞品功能更新（{target}，新增 {len(fresh)} 条）并记入台账")

if __name__ == "__main__":
    main()
