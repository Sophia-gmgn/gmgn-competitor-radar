#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞品功能更新（#2 + #4 + #3）→ Confluence
=========================================
读 data/feature_updates.json，按【竞品分组】渲染成好看的更新页：
顶部概览条 + 每家一个区块；功能更新/集成用绿色卡片突出，活动/公告用朴素文字弱化。
按固定 pageId 覆盖写、保留页面标题。

需要：ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN / FEATURE_UPDATES_PAGE_ID
（未配 pageId 时安静跳过。）
"""
import os
import sys
import re
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, now_cst_str, load_config
from common.confluence import Confluence, esc, panel, status_lozenge, expand, table

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
MAX_ITEMS_PER_COMP = int(os.environ.get("FEATURE_UPDATES_MAX_PER_COMP", "40"))

TYPE_COLOUR = {"功能更新": "Green", "集成": "Purple", "活动": "Yellow", "公告": "Blue", "其它": None}
IMPORTANT = {"功能更新", "集成"}          # 这两类用绿色卡片突出
PRIO_COLOUR = {"高": "Red", "中": "Yellow", "低": "Grey"}


def _src_link(it):
    return f' &nbsp;·&nbsp; <a href="{esc(it.get("url"))}">原文</a>' if it.get("url") else ""


def _norm_name(s):
    s = (s or "").lower().strip()
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)          # 去括号注释
    s = re.sub(r"[\s\u3000._\-]", "", s)
    return s


def _build_tier_map(directory):
    """从 config 的 directory 建 归一名→(展示名, 档位) 映射：统一各源竞品名 + 判核心/次要。"""
    m = {}
    for d in directory or []:
        m[_norm_name(d.get("label", ""))] = (str(d.get("label", "")).strip(), d.get("tier", "minor"))
    return m


def _canon_tier(competitor, tier_map):
    return tier_map.get(_norm_name(competitor), (str(competitor or "?"), "minor"))


def _rel_label(d, today):
    from datetime import date
    try:
        y, mo, da = map(int, d.split("-"))
        ty, tmo, tda = map(int, today.split("-"))
        diff = (date(ty, tmo, tda) - date(y, mo, da)).days
    except Exception:
        return ""
    return {0: "今天", 1: "昨天", 2: "前天"}.get(diff, "")


def _prio_lozenge(p):
    p = (p or "").strip()
    return " " + status_lozenge(p + "优", PRIO_COLOUR[p]) if p in PRIO_COLOUR else ""


def _item_card(it, show_comp=True):
    typ = it.get("type", "其它")
    tag = status_lozenge(typ, TYPE_COLOUR.get(typ))
    prio = _prio_lozenge(it.get("priority"))
    comp = f'<strong>{esc(it.get("_canon") or it.get("competitor", ""))}</strong> &nbsp; ' if show_comp else ""
    title = esc(it.get("title", ""))
    summary = esc(it.get("summary", ""))
    src = _src_link(it)
    if typ in IMPORTANT:
        inner = (f'<p>{comp}{tag}{prio}</p>'
                 f'<p><strong>{title}</strong></p>'
                 f'<p>{summary}{src}</p>')
        return panel("tip", inner)
    return (f'<p>{comp}{tag}{prio} &nbsp; <strong>{title}</strong></p>'
            f'<p>{summary}{src}</p>')


def _is_webjs(it):
    u = str(it.get("url", ""))
    return u.endswith(".js") or "/assets/" in u


def _webjs_collapse(comp, wj_items):
    """某竞品 web_js 官网弹窗（当前状态、无逐条日期）→ 概述 + 折叠详情。"""
    n = len(wj_items)
    kw = "、".join(_short_kw(it.get("title", "")) for it in wj_items[:4] if it.get("title"))
    overview = (f'<p><strong>{esc(comp)} · 官网弹窗当前 {n} 项功能</strong>'
                f' &nbsp;<sub>涵盖 {esc(kw)} 等</sub></p>')
    rows = []
    for it in wj_items:
        typ = it.get("type", "其它")
        tag = status_lozenge(typ, TYPE_COLOUR.get(typ))
        rows.append(f'<p>{tag} <strong>{esc(it.get("title",""))}</strong></p>'
                    f'<p>{esc(it.get("summary",""))}</p>')
    return panel("tip", overview) + expand(f"展开 {esc(comp)} 全部 {n} 项弹窗更新", "".join(rows))


def _short_kw(title):
    """从标题里抽一个短关键词（去掉竞品名和'正式上线'等）。"""
    import re
    t = re.sub(r"(DeBot|Debot|正式上线|上线|功能|支持|新增|全新|系统)", "", title)
    t = t.strip("　 ·！!。").strip()
    t = re.split(r"[，,、（(]", t)[0].strip()
    return t[:12] if t else title[:12]


def _mon_lozenge(mon):
    """② 接入状态：非「待接入」＝已接，绿色标签；否则默认灰。"""
    m = (mon or "").strip()
    if not m or m in ("待接入", "未接入", "-"):
        return status_lozenge("待接入")
    return status_lozenge(m, "Green")


def _directory_table(rows):
    headers = ["竞品", "官方 X", "官网", "Telegram", "Discord", "② 接入状态"]
    trows = []
    for d in rows:
        trows.append([
            f'<strong>{esc(d.get("label", ""))}</strong>',
            esc(d.get("x", "") or "—"),
            esc(d.get("site", "") or "—"),
            esc(d.get("tg", "") or "—"),
            esc(d.get("discord", "") or "—"),
            _mon_lozenge(d.get("monitor", "")),
        ])
    return table(headers, trows)


def render_directory(directory):
    """页面底部：核心 / 次要竞品监控清单（从 config 的 feature_updates.directory 渲染，供参考）。"""
    core = [d for d in directory if d.get("tier") == "core"]
    minor = [d for d in directory if d.get("tier") == "minor"]
    out = ["<hr/>",
           "<h2>竞品监控清单（核心 / 次要）</h2>",
           panel("info",
                 "<p>监控对象与入口速查。「待核 / 待查」= 待从官网核实，空＝待补。"
                 "本清单由 <code>config.yaml</code> 的 <code>feature_updates.directory</code> 维护"
                 "（本页每次整页重写，改这里请改 config）。</p>")]
    if core:
        out.append("<h3>核心竞品（重点监控）</h3>")
        out.append(_directory_table(core))
    if minor:
        out.append("<h3>次要竞品（泛关注）</h3>")
        out.append(_directory_table(minor))
    return "".join(out)


def render_page(store, directory=None, show_directory=False):
    now = now_cst_str()
    today = now[:10]
    total = len(store)
    tier_map = _build_tier_map(directory)

    dates_all = sorted({it.get("date", "") for it in store if it.get("date")}, reverse=True)
    comp_set = {_canon_tier(it.get("competitor", ""), tier_map)[0] for it in store}
    parts = [panel("info",
                   f"<p>竞品功能更新看板 · 共 <strong>{total}</strong> 条 &nbsp;·&nbsp; "
                   f"覆盖 <strong>{len(comp_set)}</strong> 家竞品 &nbsp;·&nbsp; "
                   f"最近更新 {esc(dates_all[0]) if dates_all else '—'}</p>"
                   f"<p><sub>三源汇总（官网弹窗 · 官方 X · 社群页）· 自动更新于 {esc(now)}（UTC+8）· 请勿手动编辑</sub></p>")]

    if not store:
        parts.append(panel("note", "<p>暂无功能更新（等首次抓取写入）。</p>"))
        if show_directory and directory:
            parts.append(render_directory(directory))
        return "".join(parts)

    # canonical 名 + tier（把三源竞品名统一、判核心/次要）
    for it in store:
        it["_canon"], it["_tier"] = _canon_tier(it.get("competitor", ""), tier_map)

    # 按日期分组（新→旧）；无日期归到末尾
    from collections import defaultdict, OrderedDict
    by_date = defaultdict(list)
    for it in store:
        by_date[it.get("date", "") or "未标注日期"].append(it)
    ordered_dates = sorted(by_date.keys(), key=lambda d: (d != "未标注日期", d), reverse=True)

    for d in ordered_dates:
        items = by_date[d]
        rel = _rel_label(d, today)
        rel_html = f'　<sub>{rel}</sub>' if rel else ""
        parts.append(f'<h2>{esc(d)}{rel_html}　<sub>{len(items)} 条</sub></h2>')

        for tier_key, tier_label in (("core", "核心竞品"), ("minor", "次要竞品")):
            titems = [x for x in items if x["_tier"] == tier_key]
            if not titems:
                continue
            parts.append(f'<p><strong>{tier_label}</strong></p>')
            wj = [x for x in titems if _is_webjs(x)]
            others = [x for x in titems if not _is_webjs(x)]
            # 卡片：同竞品聚一起、竞品内「功能更新/集成」在前
            others.sort(key=lambda x: (x["_canon"], x.get("type") not in IMPORTANT))
            for it in others:
                parts.append(_item_card(it))
            # web_js 官网弹窗按竞品折叠（放在当天该档位卡片之后）
            wjc = OrderedDict()
            for x in wj:
                wjc.setdefault(x["_canon"], []).append(x)
            for comp, cit in wjc.items():
                parts.append(_webjs_collapse(comp, cit))

    if show_directory and directory:
        parts.append(render_directory(directory))
    return "".join(parts)


def main():
    load_dotenv()
    enable_truststore()
    pid = os.environ.get("FEATURE_UPDATES_PAGE_ID", "").strip()
    if not pid:
        print("未设置 FEATURE_UPDATES_PAGE_ID —— 跳过写 Confluence"
              "（把「竞品功能更新」页的 pageId 填进来即可）。")
        return
    store = load_store(DATA_FILE)
    fu = load_config().get("feature_updates", {}) or {}
    directory = fu.get("directory", []) or []          # 始终用于判核心/次要（即使不在页面显示表）
    show_dir = fu.get("show_directory", False)
    Confluence().update_body(pid, render_page(store, directory, show_dir),
                             msg="自动更新 竞品功能更新", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {pid}（{len(store)} 条，按日期分组）")


if __name__ == "__main__":
    main()
