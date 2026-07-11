#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品功能更新（#2 + #4 + #3）—— 抓取 + 归纳。"""
import os
import sys
import json
import hashlib
import pathlib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
from common.util import (load_dotenv, enable_truststore, load_config,
                         load_store, save_store, merge_by_id, today_cst)
from common.xai import call_grok, DEFAULT_MODEL
from feature_updates.sources import read_tg_channel, read_discord_channel

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
TYPE_OK = {"新功能", "优化", "集成", "其它"}


def build_prompt(label, source_kind, posts):
    block = "\n\n".join(
        f"[{i+1}] 日期 {p.get('ts','')[:10]}｜链接 {p.get('url','')}\n{p['text']}"
        for i, p in enumerate(posts))
    return f"""以下是竞品 {label} 最近的公告原文（来自其官方 {source_kind}）。请从中挑出【真正的产品功能更新】并归纳成条目。

原文（每条以序号分隔，含日期和链接）：
{block}

规则：
1. 只保留【功能 / 产品层面的更新】：新功能上线、功能优化、新增链 / 交易对 / 集成、重要产品变更、重要版本发布。
2. 排除：纯活动 / 抽奖 / 空投喊话、行情喊单、"验证你是人类 / 入群" 这类提示、招聘或纯合作软文、单纯转发别人的东西、重复置顶、纯预告("TOMORROW"之类没有实质内容的)。
3. 把同一个功能的多条合并成一条。
4. 【标题 title 和摘要 summary 必须用简体中文书写】，即使原文是英文也要翻译成中文；只有产品名、币种、专有名词（如 Banana Predict、Polymarket、BSC、Solana）可保留英文原文。
5. 每条输出一个对象：
   - competitor: 固定填 "{label}"
   - title: 一句简体中文说清出了什么新功能
   - summary: 2-3 句简体中文，这个功能是什么 + 对用户的价值（就事论事，别抄营销词）
   - date: 该更新的日期 YYYY-MM-DD（用原文里对应那条的日期）
   - type: "新功能" / "优化" / "集成" / "其它"
   - url: 原文链接（用我在对应原文里给的那条链接；没有就填空字符串）
6. 没有任何真正的功能更新，就返回空数组 []。
只输出一个 JSON 数组，不要解释文字、不要 markdown 代码块标记。"""


def norm_item(it, label):
    it["competitor"] = label
    if it.get("type") not in TYPE_OK:
        it["type"] = "其它"
    it["title"] = str(it.get("title", "")).strip()
    it["summary"] = str(it.get("summary", "")).strip()
    it["url"] = str(it.get("url", "")).strip()
    it["date"] = str(it.get("date") or today_cst())[:10]
    basis = f"{label}|{it['title'][:50]}|{it['date']}"
    it["_id"] = "f:" + hashlib.md5(basis.encode("utf-8")).hexdigest()[:16]
    return it


def fetch_all(cfg, hours, model, api_key):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    dc_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    competitors = cfg.get("competitors", []) or []
    all_items, failed = [], []
    with httpx.Client() as client:
        for c in competitors:
            label = c.get("label") or c.get("key")
            posts, kinds = [], []

            tg = str(c.get("telegram_channel") or "").strip()
            if tg:
                got = read_tg_channel(tg, cutoff, client=client)
                posts += got
                if got:
                    kinds.append("Telegram 公告频道")
                print(f"  [{label}] TG {tg}: {len(got)} 条原文")

            dcid = str(c.get("discord_channel_id") or "").strip()
            if dcid:
                if not dc_token:
                    print(f"  [{label}] 配了 Discord 频道但缺 DISCORD_BOT_TOKEN，跳过该源",
                          file=sys.stderr)
                else:
                    got = read_discord_channel(dc_token, dcid, cutoff, client=client)
                    posts += got
                    if got:
                        kinds.append("Discord 公告频道")
                    print(f"  [{label}] Discord {dcid}: {len(got)} 条原文")

            if not posts:
                print(f"  [{label}] 近 {hours}h 无原文，跳过归纳")
                continue

            source_kind = " / ".join(kinds) if kinds else "官方频道"
            res = call_grok(client, api_key, build_prompt(label, source_kind, posts), model=model)
            if not res["ok"]:
                failed.append(f"{label}: {res['err']}")
                print(f"   ⚠️ {label} 归纳失败：{res['err']}")
                continue
            items = [norm_item(it, label) for it in res["items"]
                     if isinstance(it, dict) and it.get("title")]
            all_items += items
            u = res["usage"]
            ti = u.get("input_tokens") or u.get("prompt_tokens") or 0
            to_ = u.get("output_tokens") or u.get("completion_tokens") or 0
            print(f"   → 归纳出 {len(items)} 条功能更新｜tokens {ti}+{to_}")
            for it in items:
                print(f"      [{it['type']}] {it['title'][:60]}")
    return all_items, failed


def main():
    load_dotenv()
    enable_truststore()
    cfg = load_config().get("feature_updates", {}) or {}
    hours = int(os.environ.get("FEATURE_UPDATES_HOURS", cfg.get("hours", 168)))
    model = os.environ.get("GROK_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        print("缺少 XAI_API_KEY", file=sys.stderr)
        sys.exit(1)
    competitors = cfg.get("competitors", []) or []
    if not competitors:
        print("config.yaml 里 feature_updates.competitors 为空", file=sys.stderr)
        sys.exit(1)

    print("=== 竞品功能更新 · 抓取 ===")
    print(f"模型 {model} | 回看 {hours}h | 竞品 {len(competitors)} 个\n")
    items, failed = fetch_all(cfg, hours, model, api_key)

    pathlib.Path("feature_updates_result.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    store = load_store(DATA_FILE)
    before = len(store)
    added = merge_by_id(store, items)
    save_store(DATA_FILE, store)
    print("\n=== 汇总 ===")
    print(f"本次功能更新 {len(items)} 条；底稿 {before} → {len(store)}（新增 {added}）")
    if failed:
        print(f"⚠️ 失败：{failed}")


if __name__ == "__main__":
    main()
