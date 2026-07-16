#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品 X 话题 → Slack（读 store · heat=高 · 每主体相似度合并 · 取前10 · 台账去重）"""
import os, re, sys, pathlib
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common.util import (load_dotenv, enable_truststore, load_store, load_config, today_cst, CST)
from common.slack import (slack_from_env, post_blocks, section, divider,
                          context, link, mrkdwn_escape, pack_units, cap_message_blocks,
                          load_state, save_state)

DATA_FILE   = os.environ.get("X_TOPICS_DATA", "data/x_topics.json")
LEDGER_FILE = os.environ.get("PUSH_LEDGER", "state/push_ledger.json")
PAGE_URL    = os.environ.get("X_TOPICS_PAGE_URL",
    "https://pionex.atlassian.net/wiki/spaces/~712020158205a1add2439fae73253b196308bc/pages/2303656137")
LEDGER_KEEP_DAYS = 3
TOP_N = 10
SIM_TH = 0.55
SENT_EMOJI = {"正面": "🔥", "负面": "💢", "中性": "💬"}

def _norm_name(s):
    s = (s or "").lower().strip()
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)
    return re.sub(r"[\s\u3000._\-]", "", s)

def _core_set():
    fu = load_config().get("feature_updates", {}) or {}
    return {_norm_name(d.get("label", "")) for d in (fu.get("directory", []) or []) if d.get("tier") == "core"}

def _prune_ledger(led):
    cutoff = (datetime.now(CST) - timedelta(days=LEDGER_KEEP_DAYS)).strftime("%Y-%m-%d")
    led["posted"] = {k: v for k, v in (led.get("posted", {}) or {}).items() if v >= cutoff}
    return led

def _nt(t): return re.sub(r"[，。、,.\s（）()]", "", t or "")
def _sim(a, b):
    a, b = _nt(a), _nt(b)
    if not a or not b: return 0.0
    if a in b or b in a: return 0.95
    return SequenceMatcher(None, a, b).ratio()

def _merge(topics):
    """每主体内：相似度≥SIM_TH 的合并成一条，保留 topic 最长的、合并 examples 与 _id 列表。"""
    bysub = defaultdict(list)
    for t in topics: bysub[t.get("subject")].append(t)
    out = []
    for sub, items in bysub.items():
        clusters = []
        for it in items:
            for cl in clusters:
                if _sim(it.get("topic"), cl[0].get("topic")) >= SIM_TH:
                    cl.append(it); break
            else:
                clusters.append([it])
        for cl in clusters:
            rep = dict(max(cl, key=lambda x: len(x.get("topic", ""))))
            ex = []
            for x in cl: ex.extend(x.get("examples", []) or [])
            rep["examples"] = list(dict.fromkeys(ex))
            rep["_ids"] = [x.get("_id") for x in cl if x.get("_id")]
            rep["_merged"] = len(cl)
            out.append(rep)
    return out

def build_blocks(day, rows):
    blocks = [section(f"📣 *竞品 X 话题* · {day} · 高热 {len(rows)} 条"), divider()]
    units = []
    for it in rows:
        emo = SENT_EMOJI.get(it.get("sentiment"), "⚪")
        head = f"{emo} *{mrkdwn_escape(it.get('subject'))}*"
        body = mrkdwn_escape(it.get("topic", ""))
        piece = f"{head}\n{body}"
        exs = it.get("examples", []) or []
        if exs:
            piece += "　" + " ".join(link(u, f"原推{i+1}") for i, u in enumerate(exs[:3]))
        units.append(piece)
    blocks.extend(pack_units(units)); blocks.append(divider())
    blocks.append(section(f"📣 *更多热门话题，点这里* 👉 {link(PAGE_URL, 'X 话题看板')}"))
    return cap_message_blocks(blocks, PAGE_URL), f"竞品 X 话题 {day}：{len(rows)} 条"

def main():
    load_dotenv(); enable_truststore()
    dry = os.environ.get("X_TOPICS_DRY_RUN", "0") == "1"
    slack = slack_from_env()
    if not slack and not dry:
        print("未配置 Slack（SLACK_BOT_TOKEN + SLACK_CHANNEL）—— 跳过。"); return
    core, store = _core_set(), load_store(DATA_FILE)
    dates = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    target = os.environ.get("X_TOPICS_TARGET_DATE", "").strip() or today_cst()
    if target not in dates: target = dates[0] if dates else target
    hi = [it for it in store if it.get("date") == target and it.get("heat") == "高"]
    merged = _merge(hi)
    # 排序：自家 GMGN → 核心竞品 → 其余
    merged.sort(key=lambda x: (_norm_name(x.get("subject")) != "gmgn",
                               _norm_name(x.get("subject")) not in core,
                               x.get("subject")))
    # 台账去重（合并组里任一 _id 推过 → 整组算已推）
    led = _prune_ledger(load_state(LEDGER_FILE)); posted = led.get("posted", {})
    fresh = [it for it in merged if not any(_id in posted for _id in it.get("_ids", []))]
    rows = fresh[:TOP_N]
    if not rows:
        print(f"[{target}] 无新增高热话题（今日均已推）—— 不发送。"); return
    blocks, fb = build_blocks(target, rows)
    if dry:
        print("===== DRY_RUN（不发送、不写台账）=====\nfallback:", fb, "\n")
        for it in rows:
            emo = SENT_EMOJI.get(it.get("sentiment"), "⚪")
            mg = f"（合并{it['_merged']}）" if it.get("_merged", 1) > 1 else ""
            print(f"{emo} 【{it.get('subject')}】{it.get('topic')}{mg}  原推×{len(it.get('examples',[]))}")
        print(f"\n（高热合并后共 {len(merged)} 条，取前 {TOP_N}）")
        return
    if post_blocks(slack, blocks, fb):
        for it in rows:
            for _id in it.get("_ids", []): posted[_id] = target
        led["posted"] = posted; save_state(LEDGER_FILE, led)
        print(f"✓ 已发送 竞品 X 话题（{target}，{len(rows)} 条）并记入台账")

if __name__ == "__main__":
    main()
