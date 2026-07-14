#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞品功能更新（#2 + #4 + #3）—— 抓取 + 归纳
==========================================
读 config 里各竞品的源（公开 TG 频道 / Discord 频道）→ 把公告原文交给 Grok，
挑出【真正的功能更新】、过滤活动喊单/验证提示、归纳成条目 → 去重写 data/feature_updates.json。

单独运行 = 测试模式：抓一遍 → 打印 → 写 feature_updates_result.json + 合并进底稿。
  python feature_updates/fetch.py   （或 python -m feature_updates.fetch）

需要：XAI_API_KEY；含 Discord 源时还需 DISCORD_BOT_TOKEN。
可选：GROK_MODEL、FEATURE_UPDATES_HOURS（默认取 config，168h=7天）。
"""
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
from feature_updates.sources import read_tg_channel, read_discord_channel, read_web_js

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
TYPE_OK = {"功能更新", "活动", "集成", "公告", "其它"}


def build_prompt(label, source_kind, posts):
    block = "\n\n".join(
        f"[{i+1}] 日期 {(p.get('ts') or '')[:10]}｜链接 {p.get('url','')}\n{p['text']}"
        for i, p in enumerate(posts))
    return f"""以下是竞品 {label} 官方 {source_kind}最近发布的公告原文。请把【每一条】都翻译成简体中文并做简要解读，逐条整理成条目。

原文（每条以序号分隔，含日期和链接）：
{block}

规则：
1. 【每条公告都要保留并解读】——不管是功能更新、活动、规则说明、集成、@所有人通知等，都翻译整理，不要自行判断"重不重要"而丢弃。
2. 只在下列情况下才跳过某条：① 纯粹是"点击验证你是人类 / 入群"这类机器人验证提示；② 与上一条内容完全重复的置顶转发。除此之外一律保留。
3. 【同一件事只输出一条】：如果多条公告在讲同一个产品 / 同一个功能（例如"预告 → 正式上线 → 补充说明"其实是同一件事），必须合并成且**仅合并成一条**，以信息最全的那条为准；**绝对不要输出两条在讲同一件事的条目**。
4. 【标题 title 和摘要 summary 必须用简体中文书写】，原文是英文也要翻译成中文；只有产品名、币种、专有名词（如 Banana Predict、Polymarket、BSC、Solana、cashback）可保留英文原文。
5. 每条输出一个对象：
   - competitor: 固定填 "{label}"
   - title: 一句简体中文概括这条公告在说什么
   - summary: 2-3 句简体中文，把这条公告的内容说清楚 + 简要解读它的意思 / 影响（就事论事，别抄营销词）
   - date: 该公告的日期 YYYY-MM-DD（用原文里对应那条的日期）
   - type: 给这条归个类，从这几个里选一个："功能更新"（新功能 / 优化 / 版本发布）/ "集成"（新增链 / 交易对 / 合作接入）/ "活动"（抽奖 / 空投 / 竞赛 / 奖励）/ "公告"（规则说明 / 通知 / 喊话）/ "其它"
   - url: 原文链接（用我在对应原文里给的那条链接；没有就填空字符串）
6. 如果原文全部是验证提示 / 空内容，才返回空数组 []。
只输出一个 JSON 数组，不要解释文字、不要 markdown 代码块标记。"""


def norm_item(it, label):
    it["competitor"] = label
    if it.get("type") not in TYPE_OK:
        it["type"] = "其它"
    it["title"] = str(it.get("title", "")).strip()
    it["summary"] = str(it.get("summary", "")).strip()
    it["url"] = str(it.get("url", "")).strip()
    it["date"] = str(it.get("date") or today_cst())[:10]
    # 去重键：优先用原文链接（跨次稳定，不随 Grok 措辞变化）；没有链接再退回 标题+日期
    key = it["url"].rstrip("/") if it["url"] else f"{it['title'][:50]}|{it['date']}"
    it["_id"] = "f:" + hashlib.md5(f"{label}|{key}".encode("utf-8")).hexdigest()[:16]
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

            wjs = str(c.get("web_js") or "").strip()
            if wjs:
                got = read_web_js(wjs, cutoff, client=client)
                posts += got
                if got:
                    kinds.append("官网内置公告")
                print(f"  [{label}] 网站JS {wjs}: {len(got)} 条原文")

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
