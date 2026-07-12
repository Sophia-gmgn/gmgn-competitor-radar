#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品交易数据 → Slack（未配置则安静跳过）。"""
import os, sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common.util import load_dotenv, enable_truststore, now_cst_str
DATA_FILE = os.environ.get("TRADING_DATA_FILE", "data/trading_data.json")
def usd(v):
    if v is None: return "—"
    a=abs(v)
    if a>=1e9: return f"${v/1e9:.2f}B"
    if a>=1e6: return f"${v/1e6:.2f}M"
    if a>=1e3: return f"${v/1e3:.1f}K"
    return f"${v:,.0f}"
def main():
    load_dotenv(); enable_truststore()
    token=os.environ.get("SLACK_BOT_TOKEN","").strip(); channel=os.environ.get("SLACK_CHANNEL","").strip()
    if not token or not channel:
        print("未配置 SLACK_BOT_TOKEN / SLACK_CHANNEL —— 跳过 Slack 推送。"); return
    try:
        from common.slack import post_blocks
    except Exception as e:
        print(f"Slack 模块不可用，跳过：{e}"); return
    snap=json.load(open(DATA_FILE,encoding="utf-8"))
    items=sorted(snap.get("items",[]), key=lambda it:(it.get("vol") or {}).get("d30") or 0, reverse=True)
    lines=[f"*竞品交易量*（{now_cst_str()}）"]
    for i,it in enumerate(items):
        tag=" ⭐" if it.get("self") else ""
        lines.append(f"{i+1}. {it['label']}{tag}  30d {usd((it.get('vol') or {}).get('d30'))}")
    blocks=[{"type":"section","text":{"type":"mrkdwn","text":"\n".join(lines)}}]
    try:
        post_blocks(token, channel, blocks, text="竞品交易量"); print("✓ 已推送 Slack")
    except Exception as e:
        print(f"Slack 推送失败（不影响主流程）：{e}")
if __name__ == "__main__":
    main()
