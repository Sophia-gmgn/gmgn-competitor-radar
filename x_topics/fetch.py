#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X 话题日报（#1 · Haze）—— 抓取 + 归纳
=====================================
广搜 X 上关于「GMGN + 竞品」的公开讨论（不是盯固定账号），让 Grok 归纳当天在讨论哪些
话题、过滤掉纯邀请链接/推荐码导流帖，输出结构化 JSON。

单独运行 = 测试模式：抓一遍 → 打印 → 写 x_topics_result.json + 合并进 data/x_topics.json。
  先用它验证「覆盖度 / 费用 / 话题质量」，调好 config.yaml 的 terms 再上定时。
    python x_topics/fetch.py         （或 python -m x_topics.fetch）

需要：XAI_API_KEY
可选：GROK_MODEL(默认 grok-4.5)、X_TOPICS_HOURS(默认取 config.yaml)
"""
import os
import sys
import json
import hashlib
import pathlib
from datetime import datetime, timezone, timedelta

# 让 `python x_topics/fetch.py` 和 `python -m x_topics.fetch` 都能 import common
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
from common.util import (load_dotenv, enable_truststore, load_config,
                         load_store, save_store, merge_by_id, today_cst)
from common.xai import call_grok, x_search_tool, DEFAULT_MODEL

DATA_FILE = os.environ.get("X_TOPICS_DATA", "data/x_topics.json")

HEAT_OK = {"高", "中", "低"}
SENT_OK = {"正面", "中性", "负面"}


def build_prompt(label, terms, hours, since_utc, max_topics, is_self):
    who = "你们自己（GMGN）" if is_self else f"竞品 {label}"
    frame = ("这是自身舆情监控：看用户 / 社区当前在议论 GMGN 的什么。" if is_self
             else f"这是竞品舆情监控：看用户 / 社区当前在议论 {label} 的什么。")
    return f"""请检索最近 {hours} 小时（约 {since_utc} UTC 至今）X 上关于「{label}」的公开讨论。
搜索线索（名称 / 别名 / 官方号）：{terms}

你在为 GMGN（多链 meme 交易终端）做竞品与自身舆情监控。本次对象是 {who}。{frame}
把这段时间大家讨论的**主要话题**归纳出来（最多 {max_topics} 个，合并同类），每个话题给：
- date: 当天日期，格式 YYYY-MM-DD
- subject: 固定填 "{label}"
- topic: 一句简体中文概括这个话题在讨论什么（就事论事，别堆营销词；**统一用简体中文表述，不要中英混写**，如统一写"交易市场"而非"Trading Market"）
- heat: 讨论热度 "高" / "中" / "低"（相对这段时间该对象的讨论量）
- sentiment: 整体情绪 "正面" / "中性" / "负面"
- examples: 2–3 条代表性推文链接（形如 https://x.com/.../status/...；确实拿不到就填 []）

只输出一个 JSON 数组，不要任何解释文字、不要 markdown 代码块标记。

硬性要求：
1. 【只算加密 / meme 交易语境下、确实关于「{label}」的讨论】——忽略同名的无关内容（如同名的音乐人、物理名词、网络俚语、其它行业公司等）；**特别排除游戏 / 卡牌 / 抽卡 / Gacha / 宝可梦 / NFT 收藏等非交易内容**（如 "Jupiter Gacha 抽卡" 这类与 meme 交易无关的话题一律不要）。
2. 【排除拉新导流帖】：主体是推荐码 / 邀请链接 / referral / "用我的码上车" / "点我链接注册领奖励" 这类拉新帖，不要当作话题，也不要放进 examples；正常讨论里顺带提到产品的，保留。
3. 【排除自动 bot / 刷屏帖】：由自动化账号批量发布的交易链接、行情播报、"某某 volume / 买入卖出信号 / 交易提醒"这类模板化机器人帖（例如 RobinhoodVolume 这类持续自动发交易链接的账号），不是真实用户讨论，不要当话题、也不要放进 examples。
4. 【合并近似话题】：同一件事只归纳成**一条**话题，不要产出多条措辞不同但意思相同的条目（例如"UX 流畅"和"界面体验好"应合并为一条）。
5. 【不编造】：这段时间没有真实讨论就直接返回空数组 []，不要用训练知识补。
6. 最终必须是能被 JSON.parse 直接解析的合法 JSON。"""


def norm_topic(it, label, run_date):
    it["subject"] = label                      # 以配置为准
    it["date"] = run_date                       # 话题是当次归纳的聚合，统一记为运行日（北京）
    if it.get("heat") not in HEAT_OK:
        it["heat"] = "低"
    if it.get("sentiment") not in SENT_OK:
        it["sentiment"] = "中性"
    ex = it.get("examples")
    it["examples"] = [u for u in ex if isinstance(u, str) and u.strip()][:3] if isinstance(ex, list) else []
    basis = f"{run_date}|{label}|{(it.get('topic', '') or '')[:60]}"
    it["_id"] = "t:" + hashlib.md5(basis.encode("utf-8")).hexdigest()[:16]
    return it


def fetch_all(api_key, subjects, hours, max_topics, model=None):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    from_date = since.strftime("%Y-%m-%d")
    to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    since_utc = since.strftime("%Y-%m-%d %H:%M")
    run_date = today_cst()

    all_items, failed = [], []
    total_in = total_out = total_src = 0
    with httpx.Client() as client:
        for s in subjects:
            label = s.get("label") or s.get("key")
            terms = s.get("terms") or label
            is_self = bool(s.get("self"))
            prompt = build_prompt(label, terms, hours, since_utc, max_topics, is_self)
            res = call_grok(client, api_key, prompt,
                            tools=[x_search_tool(from_date, to_date)], model=model)  # 广搜，不限 handle
            if not res["ok"]:
                failed.append(f"{label}: {res['err']}")
                print(f"  ⚠️ {label} 失败：{res['err']}")
                continue
            got = [norm_topic(it, label, run_date) for it in res["items"] if isinstance(it, dict)]
            all_items.extend(got)
            u = res["usage"]
            ti = u.get("input_tokens") or u.get("prompt_tokens") or 0
            to_ = u.get("output_tokens") or u.get("completion_tokens") or 0
            total_in += ti
            total_out += to_
            total_src += len(res["citations"])
            print(f"  {label}: {len(got)} 个话题｜引用源 {len(res['citations'])}｜tokens {ti}+{to_}")
            for it in got:
                print(f"    [{it['heat']}/{it['sentiment']}] {it['topic'][:70]}")
    print(f"\n引用源合计（≈搜索用量，费用主要看这个）：{total_src}｜tokens 合计 {total_in}+{total_out}")
    return all_items, failed


def _norm_text(t):
    """归一化话题文字用于相似比较：去标点/空格/大小写。"""
    import re
    t = (t or "").lower()
    t = re.sub(r"[\s\u3000,.，。、；;:：!！?？·\-—_()（）\"'\[\]【】]", "", t)
    return t


def _similar(a, b):
    """两段话题文字的相似度 0~1（基于字符集合 + 顺序）。"""
    from difflib import SequenceMatcher
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 一方包含另一方（如“提现等待24小时”⊂“用户抱怨提现需要等待24小时”）
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def dedup_similar_same_day(store, threshold=0.82):
    """同一天 + 同主体下，话题文字相似度≥threshold 的只保留一条（保留话题更长/信息更全的）。
    返回删除条数。"""
    from collections import defaultdict
    groups = defaultdict(list)
    for it in store:
        groups[(it.get("date"), it.get("subject"))].append(it)

    to_remove = set()
    for (_, _), items in groups.items():
        kept = []
        for it in items:
            dup_of = None
            for k in kept:
                if _similar(it.get("topic"), k.get("topic")) >= threshold:
                    dup_of = k
                    break
            if dup_of is None:
                kept.append(it)
            else:
                # 保留话题文字更长（信息更全）的那条，合并 examples
                longer = it if len(it.get("topic", "")) > len(dup_of.get("topic", "")) else dup_of
                shorter = dup_of if longer is it else it
                ex = (longer.get("examples") or []) + (shorter.get("examples") or [])
                seen = set(); merged = []
                for u in ex:
                    if u and u not in seen:
                        seen.add(u); merged.append(u)
                longer["examples"] = merged[:3]
                to_remove.add(id(shorter))
                if longer is it and dup_of in kept:
                    kept[kept.index(dup_of)] = it

    if to_remove:
        store[:] = [it for it in store if id(it) not in to_remove]
    return len(to_remove)


def main():
    load_dotenv()
    enable_truststore()
    cfg = load_config().get("x_topics", {}) or {}
    subjects = cfg.get("subjects", []) or []
    hours = int(os.environ.get("X_TOPICS_HOURS", cfg.get("hours", 24)))
    max_topics = int(cfg.get("per_subject_topics", 6))
    model = os.environ.get("GROK_MODEL", DEFAULT_MODEL)

    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        print("缺少 XAI_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)
    if not subjects:
        print("config.yaml 里 x_topics.subjects 为空", file=sys.stderr)
        sys.exit(1)

    print(f"=== X 话题日报 · 抓取 ===")
    print(f"模型 {model} | 回看 {hours}h | 主体 {len(subjects)} 个\n")
    items, failed = fetch_all(api_key, subjects, hours, max_topics, model)

    # 测试产物（workflow 可上传为 artifact 检查）
    pathlib.Path("x_topics_result.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    # 合并进底稿（同日 + 同主体 + 同话题 去重）
    store = load_store(DATA_FILE)
    before = len(store)
    added = merge_by_id(store, items)
    removed = dedup_similar_same_day(store)   # 再按“同天+同主体+话题相似”去重
    save_store(DATA_FILE, store)
    print(f"\n=== 汇总 ===")
    print(f"本次话题 {len(items)} 个；底稿 {before} → {len(store)}（新增 {added}，相似去重 {removed}）")
    if failed:
        print(f"⚠️ 失败主体：{failed}")


if __name__ == "__main__":
    main()
