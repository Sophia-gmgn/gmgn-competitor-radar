#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X 话题日报（#1）· 抓取 —— 广搜 X 讨论 → Grok 归纳话题 → 写 data/x_topics.json"""
import os
import sys
import json
import pathlib
import hashlib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.xai import call_grok, x_search_tool, extract_output_text, parse_json_array, DEFAULT_MODEL
from common.util import (load_dotenv, enable_truststore, load_config,
                         load_store, save_store, merge_by_id, today_cst)

DATA_FILE = os.environ.get("X_TOPICS_DATA", "data/x_topics.json")
HEAT_OK = {"高", "中", "低"}
SENT_OK = {"正面", "中性", "负面"}


def build_prompt(label, terms, hours, self_flag, max_topics):
    now = datetime.now(timezone.utc)
    since_utc = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    who = f"我们自己（{label}）" if self_flag else f"竞品 {label}"
    frame = ("这是自身舆情监控：看用户 / 社区当前在议论我们的什么。"
             if self_flag
             else f"这是竞品舆情监控：看用户 / 社区当前在议论 {label} 的什么。")
    return f"""请检索最近 {hours} 小时（约 {since_utc} UTC 至今）X 上关于「{label}」的公开讨论。
搜索线索（名称 / 别名 / 官方号）：{terms}

你在为 GMGN（多链 meme 交易终端）做竞品与自身舆情监控。本次对象是 {who}。{frame}
把这段时间大家讨论的**主要话题**归纳出来（最多 {max_topics} 个，合并同类），每个话题给：
- date: 当天日期，格式 YYYY-MM-DD
- subject: 固定填 "{label}"
- topic: 一句简体中文概括这个话题在讨论什么（就事论事，别堆营销词）
- heat: 讨论热度 "高" / "中" / "低"（相对这段时间该对象的讨论量）
- sentiment: 整体情绪 "正面" / "中性" / "负面"
- examples: 2–3 条代表性推文链接（形如 https://x.com/.../status/...；确实拿不到就填 []）

只输出一个 JSON 数组，不要任何解释文字、不要 markdown 代码块标记。

硬性要求：
1. 【只算加密 / meme 交易语境下、确实关于「{label}」的讨论】——忽略同名的无关内容（如同名的音乐人、物理名词、游戏 / 卡牌、网络俚语、其它行业公司等）。
2. 【排除拉新导流帖】：主体是推荐码 / 邀请链接 / referral / "用我的码上车" / "点我链接注册领奖励" 这类拉新帖，不要当作话题，也不要放进 examples；正常讨论里顺带提到产品的，保留。
3. 【排除自动 bot / 刷屏帖】：由自动化账号批量发布的交易链接、行情播报、"某某 volume / 买入卖出信号 / 交易提醒"这类模板化机器人帖（例如 RobinhoodVolume 这类持续自动发交易链接的账号），不是真实用户讨论，不要当话题、也不要放进 examples。
4. 【不编造】：这段时间没有真实讨论就直接返回空数组 []，不要用训练知识补。
5. 最终必须是能被 JSON.parse 直接解析的合法 JSON。"""


def norm_topic(it, label, run_date):
    it["subject"] = label
    it["date"] = run_date
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
    run_date = today_cst()
    items, failed = [], []
    for s in subjects:
        label = s.get("label", s.get("key", "?"))
        terms = s.get("terms", label)
        prompt = build_prompt(label, terms, hours, s.get("self", False), max_topics)
        res = call_grok(api_key, prompt,
                        tools=[x_search_tool(from_date, to_date)], model=model)
        if res.get("err"):
            failed.append(f"{label}: {res['err']}")
            print(f"  ⚠️ {label} 失败：{res['err']}")
            continue
        got = [norm_topic(it, label, run_date) for it in res["items"] if isinstance(it, dict)]
        cites = res.get("citations", 0)
        u = res.get("usage", {})
        print(f"  {label}: {len(got)} 个话题｜引用源 {cites}｜tokens {u.get('in',0)}+{u.get('out',0)}")
        for it in got:
            print(f"    [{it.get('heat')}/{it.get('sentiment')}] {it.get('topic')}")
        items.extend(got)
    return items, failed


def _norm_text(t):
    import re
    t = (t or "").lower()
    t = re.sub(r"[\s\u3000,.，。、；;:：!！?？·\-—_()（）\"'\[\]【】]", "", t)
    return t


def _similar(a, b):
    from difflib import SequenceMatcher
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def dedup_similar_same_day(store, threshold=0.82):
    from collections import defaultdict
    groups = defaultdict(list)
    for it in store:
        groups[(it.get("date"), it.get("subject"))].append(it)
    to_remove = set()
    for key, grp in groups.items():
        kept = []
        for it in grp:
            dup_of = None
            for k in kept:
                if _similar(it.get("topic"), k.get("topic")) >= threshold:
                    dup_of = k
                    break
            if dup_of is None:
                kept.append(it)
            else:
                longer = it if len(it.get("topic", "")) > len(dup_of.get("topic", "")) else dup_of
                shorter = dup_of if longer is it else it
                ex = (longer.get("examples") or []) + (shorter.get("examples") or [])
                seen = set()
                merged = []
                for u in ex:
                    if u and u not in seen:
                        seen.add(u)
                        merged.append(u)
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
    cfg = (load_config().get("x_topics", {}) or {})
    subjects = cfg.get("subjects", []) or []
    hours = int(cfg.get("hours", 24))
    max_topics = int(cfg.get("per_subject_topics", 6))
    model = os.environ.get("XAI_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("XAI_API_KEY", "").strip()

    print("=== X 话题日报 · 抓取 ===")
    print(f"模型 {model} | 回看 {hours}h | 主体 {len(subjects)} 个\n")

    if not api_key:
        print("未配置 XAI_API_KEY", file=sys.stderr)
        sys.exit(1)
    if not subjects:
        print("config.yaml 里 x_topics.subjects 为空", file=sys.stderr)
        sys.exit(1)

    items, failed = fetch_all(api_key, subjects, hours, max_topics, model)

    total_cites = 0
    pathlib.Path("x_topics_result.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    store = load_store(DATA_FILE)
    before = len(store)
    added = merge_by_id(store, items)
    removed = dedup_similar_same_day(store)
    save_store(DATA_FILE, store)
    print(f"\n=== 汇总 ===")
    print(f"本次话题 {len(items)} 个；底稿 {before} → {len(store)}（新增 {added}，相似去重 {removed}）")
    if failed:
        print("失败：")
        for f in failed:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
