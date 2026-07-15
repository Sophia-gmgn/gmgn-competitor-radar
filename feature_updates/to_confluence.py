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
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.util import load_dotenv, enable_truststore, load_store, now_cst_str, load_config
from common.confluence import Confluence, esc, panel, status_lozenge, expand, table

DATA_FILE = os.environ.get("FEATURE_UPDATES_DATA", "data/feature_updates.json")
MAX_ITEMS_PER_COMP = int(os.environ.get("FEATURE_UPDATES_MAX_PER_COMP", "40"))

TYPE_COLOUR = {"功能更新": "Green", "集成": "Purple", "活动": "Yellow", "公告": "Blue", "其它": None}
IMPORTANT = {"功能更新", "集成"}          # 这两类用绿色卡片突出
TYPE_EMOJI = {"功能更新": "🆕", "集成": "🔗", "活动": "🎁", "公告": "📢", "其它": "📝"}


def _src_link(it):
    return f' &nbsp;·&nbsp; <a href="{esc(it.get("url"))}">🔗 原文</a>' if it.get("url") else ""


def render_item(it):
    typ = it.get("type", "其它")
    tag = status_lozenge(typ, TYPE_COLOUR.get(typ))
    title = esc(it.get("title", ""))
    summary = esc(it.get("summary", ""))
    date = esc(it.get("date", ""))
    src = _src_link(it)
    if typ in IMPORTANT:
        # 重点：绿色卡片
        inner = (f'<p>{tag} &nbsp;<sub>{date}</sub></p>'
                 f'<p><strong>{title}</strong></p>'
                 f'<p>{summary}{src}</p>')
        return panel("tip", inner)
    # 次要：朴素文字，弱化
    return (f'<p>{tag} &nbsp;<strong>{title}</strong> &nbsp;<sub>{date}</sub></p>'
            f'<p>{summary}{src}</p>')


def _is_webjs(it):
    u = str(it.get("url", ""))
    return u.endswith(".js") or "/assets/" in u


def _webjs_block(comp, wj_items):
    """把 web_js 来源的公告打包成：概述 panel + 折叠详情（简洁、无链接）。"""
    n = len(wj_items)
    # 概述：取前几条标题里的关键词做一句话概括
    kw = "、".join(
        _short_kw(it.get("title", "")) for it in wj_items[:4] if it.get("title"))
    overview = (f'<p>🆕 <strong>{esc(comp)} 官网弹窗共 {n} 项功能更新</strong></p>'
                f'<p>涵盖 {esc(kw)} 等，点击下方展开查看全部详情 👇</p>')
    # 折叠里的详情：每条 标题 + 一句摘要，无原文链接
    rows = []
    for it in wj_items:
        typ = it.get("type", "其它")
        tag = status_lozenge(typ, TYPE_COLOUR.get(typ))
        title = esc(it.get("title", ""))
        summary = esc(it.get("summary", ""))
        rows.append(f'<p>{tag} <strong>{title}</strong></p><p>{summary}</p>')
    detail = "".join(rows)
    return panel("tip", overview) + expand(f"📋 展开查看全部 {n} 项更新详情", detail)


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


def render_page(store, directory=None):
    now = now_cst_str()
    total = len(store)

    # 按竞品分组
    comps = {}
    for it in store:
        comps.setdefault(it.get("competitor", "?"), []).append(it)

    def recent(items):
        return max((i.get("date", "") for i in items), default="")

    ordered = sorted(comps.items(), key=lambda kv: recent(kv[1]), reverse=True)

    # 顶部概览条
    if ordered:
        counts = " &nbsp;·&nbsp; ".join(f"<strong>{esc(c)}</strong> {len(items)}"
                                        for c, items in ordered)
    else:
        counts = "暂无"
    parts = [panel("info",
                   f"<p>📊 竞品功能更新监控 · 共 <strong>{total}</strong> 条 &nbsp;｜&nbsp; {counts}</p>"
                   f"<p><sub>本页由脚本自动更新，更新于 {esc(now)}（UTC+8）· 请勿手动编辑（每次整页重写）</sub></p>")]

    if not store:
        parts.append(panel("note", "<p>暂无功能更新（等首次抓取写入）。</p>"))
        if directory:
            parts.append(render_directory(directory))
        return "".join(parts)

    # 每家一个区块
    for comp, items in ordered:
        items = sorted(items, key=lambda x: x.get("date", ""), reverse=True)[:MAX_ITEMS_PER_COMP]
        # 分离 web_js 公告（打包折叠）和其它来源（逐条展示）
        wj_items = [x for x in items if _is_webjs(x)]
        other_items = [x for x in items if not _is_webjs(x)]

        n_imp = sum(1 for x in other_items if x.get("type") in IMPORTANT)
        total_comp = len(items)
        badge = f'　<sub>{total_comp} 条' + (f'，{n_imp} 条功能更新' if n_imp else '') + '</sub>'
        parts.append(f'<h2>🔹 {esc(comp)}{badge}</h2>')

        # 先放 web_js 打包块（概述+折叠）
        if wj_items:
            parts.append(_webjs_block(comp, wj_items))
        # 再逐条放其它来源
        for it in other_items:
            parts.append(render_item(it))

    if directory:
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
    # 集合页是否显示「核心/次要总表」：config 里 show_directory=true 才显示（数据始终保留在 directory）
    directory = (fu.get("directory", []) or []) if fu.get("show_directory", False) else None
    Confluence().update_body(pid, render_page(store, directory),
                             msg="自动更新 竞品功能更新", keep_title=True)
    print(f"✓ 已写入 Confluence 页 {pid}（{len(store)} 条，按竞品分组）")


if __name__ == "__main__":
    main()
