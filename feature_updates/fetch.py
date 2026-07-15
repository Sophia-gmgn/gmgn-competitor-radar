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
import re
import pathlib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
from common.util import (load_dotenv, enable_truststore, load_config,
                         load_store, save_store, merge_by_id, today_cst)
from common.xai import call_grok, x_search_tool, DEFAULT_MODEL
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
    # 去重键：普通公告用原文链接（每条独立）；web_js 提取的公告共享同一 JS 文件 url，
    # 改用标题区分（否则多条撞成一条）
    url = it["url"]
    if url and not (url.endswith(".js") or "/assets/" in url):
        key = url.rstrip("/")
    else:
        key = f"title:{it['title'][:60]}"
    it["_id"] = "f:" + hashlib.md5(f"{label}|{key}".encode("utf-8")).hexdigest()[:16]
    return it


def build_x_prompt(label, handle, hours, since_utc):
    return f"""请检索最近 {hours} 小时（约 {since_utc} UTC 至今）竞品 {label} 官方 X 账号 @{handle} 发布的推文，只挑出【产品 / 官方公告类】内容，逐条整理。

你在为 GMGN（多链 meme 交易终端）做竞品功能情报监控。只保留 @{handle} 本账号发布（含官方转发自家产品）的、属于下列性质的推文：
· 新功能 / 功能优化 / 版本发布
· 新增链 / 交易对 / 合作集成
· 空投 / 活动 / 交易竞赛 / 奖励
· 规则说明 / 重要通知

【必须排除、一条都不要】：日常喊单、行情 / K线 / 涨跌播报、GM / 打招呼、纯 meme / 玩梗、对他人的回复与互动、无关转发、KOL 营销、拉新推荐码 / 邀请链接。这些都不是功能更新。

每条输出一个对象：
- competitor: 固定填 "{label}"
- title: 一句简体中文概括（英文原文翻成中文；产品名 / 币种 / 专有名词可保留英文）
- summary: 2-3 句简体中文，说清内容 + 简要影响（就事论事，别抄营销词）
- date: 该推文日期 YYYY-MM-DD
- type: 从这几个里选一个："功能更新" / "集成" / "活动" / "公告" / "其它"
- url: 该推文链接（形如 https://x.com/{handle}/status/... ；拿不到就填空字符串）

只输出一个 JSON 数组，不要解释文字、不要 markdown 代码块标记。
硬性要求：这段时间该账号没有符合条件的公告，就直接返回空数组 []；不要用训练知识补、不要编造。"""


def _emit(all_items, label, src, res, failed):
    """把一次 Grok 结果并入 all_items 并打印；失败记入 failed。"""
    if not res["ok"]:
        failed.append(f"{label}（{src}）: {res['err']}")
        print(f"   ⚠️ {label} {src} 失败：{res['err']}")
        return
    items = [norm_item(it, label) for it in res["items"]
             if isinstance(it, dict) and it.get("title")]
    all_items += items
    u = res["usage"]
    ti = u.get("input_tokens") or u.get("prompt_tokens") or 0
    to_ = u.get("output_tokens") or u.get("completion_tokens") or 0
    print(f"   → {src}：归纳出 {len(items)} 条｜tokens {ti}+{to_}")
    for it in items:
        print(f"      [{it['type']}] {it['title'][:60]}")


# ---------------- 相似去重（同一竞品下，重复措辞的同一件事合并成一条）----------------
_FILLER = re.compile(
    r"(debot|axiom|based ?bot|trojan|photon|banana ?gun|bonkbot|maestro|bloom|"
    r"jupiter|pump\.?fun|terminal|fomo|正式上线|已上线|上线|发布|推出|新增|全新|"
    r"支持|功能|系统|更新|现已|已经)", re.I)
_PUNCT = re.compile(r"[\s\u3000,.，。、；;:：!！?？·\-—_（）()\"'“”‘’\[\]【】/|]")


def _norm_title(t):
    return _PUNCT.sub("", _FILLER.sub("", (t or "").lower()))


def _similar(a, b):
    from difflib import SequenceMatcher
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 6 and len(nb) >= 6 and (na in nb or nb in na):
        return 0.9
    return SequenceMatcher(None, na, nb).ratio()


def dedup_similar(store, threshold=0.86):
    """同一竞品下，标题相似度≥threshold 的多条合并成一条：保留 summary 最全的那条，
    date 取最新，url 补齐。只合并『重复措辞的同一件事』，不同功能不会被合并。返回合并掉的条数。"""
    from collections import defaultdict
    groups = defaultdict(list)
    for it in store:
        groups[it.get("competitor", "?")].append(it)
    to_remove = set()
    for _, items in groups.items():
        kept = []
        for it in items:
            dup = next((k for k in kept
                        if _similar(it.get("title"), k.get("title")) >= threshold), None)
            if dup is None:
                kept.append(it)
                continue
            keep = it if len(it.get("summary", "")) > len(dup.get("summary", "")) else dup
            drop = dup if keep is it else it
            keep["date"] = max(keep.get("date", ""), drop.get("date", ""))
            if not keep.get("url"):
                keep["url"] = drop.get("url", "")
            to_remove.add(id(drop))
            if keep is it:
                kept[kept.index(dup)] = it
    if to_remove:
        store[:] = [it for it in store if id(it) not in to_remove]
    return len(to_remove)


def fetch_all(cfg, hours, model, api_key):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    from_date = cutoff.strftime("%Y-%m-%d")
    to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    since_utc = cutoff.strftime("%Y-%m-%d %H:%M")

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

            # ① 社群 / 官网原文 → Grok 归纳（原有逻辑，DeBot 官网弹窗走这里）
            if posts:
                source_kind = " / ".join(kinds) if kinds else "官方频道"
                res = call_grok(client, api_key, build_prompt(label, source_kind, posts), model=model)
                _emit(all_items, label, "社群/官网", res, failed)
            else:
                print(f"  [{label}] 社群 / 官网近 {hours}h 无原文")

            # ② 官方 X（配了 x_account 才抓）→ Grok 边搜边判，只留公告，滤掉喊单行情
            xacc = str(c.get("x_account") or "").strip().lstrip("@")
            if xacc:
                print(f"  [{label}] X @{xacc}: 检索中…")
                res = call_grok(client, api_key, build_x_prompt(label, xacc, hours, since_utc),
                                tools=[x_search_tool(from_date, to_date, allowed_handles=[xacc])],
                                model=model)
                _emit(all_items, label, f"X @{xacc}", res, failed)
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
    removed = dedup_similar(store)
    save_store(DATA_FILE, store)
    print("\n=== 汇总 ===")
    print(f"本次功能更新 {len(items)} 条；底稿 {before} → {len(store)}（新增 {added}，相似去重合并 {removed}）")
    if failed:
        print(f"⚠️ 失败：{failed}")


if __name__ == "__main__":
    main()
