# -*- coding: utf-8 -*-
"""
Confluence 封装 —— 改自 Cynthia 的 grok_confluence。
REST v2（Basic Auth：邮箱 + API Token）+ 一组 storage 渲染 helper。
数据与呈现分离：每次从底稿整页重渲、按固定 pageId 覆盖写（幂等、防乱码）。
"""
import os
import sys
import html

import httpx

SITE = os.environ.get("CONFLUENCE_SITE", "pionex.atlassian.net").strip()
SPACE_ID = os.environ.get("CONFLUENCE_SPACE_ID", "224755860").strip()
API_BASE = f"https://{SITE}/wiki/api/v2"

# status 宏可用颜色：Grey / Red / Yellow / Green / Blue / Purple（None=默认灰）
def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def panel(kind, inner_html):
    """告示板：kind ∈ info / note / tip / warning。"""
    return (f'<ac:structured-macro ac:name="{kind}">'
            f'<ac:rich-text-body>{inner_html}</ac:rich-text-body>'
            '</ac:structured-macro>')


def status_lozenge(text, colour=None):
    c = f'<ac:parameter ac:name="colour">{colour}</ac:parameter>' if colour else ""
    return ('<ac:structured-macro ac:name="status">'
            f'<ac:parameter ac:name="title">{esc(text)}</ac:parameter>{c}'
            '</ac:structured-macro>')


def expand(label, inner_html):
    return ('<ac:structured-macro ac:name="expand">'
            f'<ac:parameter ac:name="title">{esc(label)}</ac:parameter>'
            f'<ac:rich-text-body>{inner_html}</ac:rich-text-body>'
            '</ac:structured-macro>')


def table(headers, rows):
    """rows 的单元格是【已就绪的 HTML】（可含链接）；表头会转义。"""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table data-layout="default"><tbody><tr>{head}</tr>{body}</tbody></table>'


class Confluence:
    def __init__(self, email=None, token=None):
        email = email or os.environ.get("ATLASSIAN_EMAIL", "").strip()
        token = token or os.environ.get("ATLASSIAN_API_TOKEN", "").strip()
        if not email or not token:
            print("缺少 ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN 环境变量", file=sys.stderr)
            sys.exit(1)
        self.c = httpx.Client(auth=(email, token),
                              headers={"Content-Type": "application/json"}, timeout=60)

    def get_page(self, pid):
        r = self.c.get(f"{API_BASE}/pages/{pid}", params={"body-format": "storage"})
        r.raise_for_status()
        return r.json()

    def update_body(self, pid, storage, msg="", keep_title=True, title=None):
        """按 pageId 覆盖写正文；默认保留页面现有标题（不动你写好的占位标题）。"""
        cur = self.get_page(pid)
        ver = cur["version"]["number"] + 1
        new_title = cur["title"] if keep_title else (title or cur["title"])
        payload = {"id": str(pid), "status": "current", "title": new_title,
                   "body": {"representation": "storage", "value": storage},
                   "version": {"number": ver, "message": msg}}
        r = self.c.put(f"{API_BASE}/pages/{pid}", json=payload)
        r.raise_for_status()
        return r.json()
