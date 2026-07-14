# -*- coding: utf-8 -*-
import hashlib
"""
功能② 读取源
============
两种源，接口统一返回 [{text, url, ts, msg_id}]：
- 公开 TG 广播频道：读网页预览 t.me/s/<channel>，无需 bot / token。
- Discord 频道：用 Bot token 读（复用 Cynthia digest.py 的读法）。读的是「你自己
  服务器里 Follow 了竞品公告频道的那个频道」。
"""
import re
import sys
import html
from datetime import datetime, timezone

import httpx

TG_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/122 Safari/537.36")
DISCORD_API = "https://discord.com/api/v10"

_URL_RE = re.compile(r'https?://[^\s<>"\)]+')
_IMG_RE = re.compile(r'\.(png|jpg|jpeg|gif|webp)(\?|$)', re.I)


def _first_url(text):
    for u in _URL_RE.findall(text or ""):
        if not _IMG_RE.search(u):
            return u.rstrip('.,)')
    return ""


def _strip_html(raw):
    x = re.sub(r'<br\s*/?>', '\n', raw)
    x = re.sub(r'<[^>]+>', '', x)
    return html.unescape(x).strip()


# ---------------- 公开 TG 广播频道 ----------------
def read_tg_channel(channel, cutoff_dt, client=None):
    """读 t.me/s/<channel>。返回近 cutoff 之后的帖子 [{text,url,ts,msg_id}]。
    若该 handle 实为群/验证门/落地页（无帖子流），返回 [] 并打印提示。"""
    url = f"https://t.me/s/{channel}"
    owns = client is None
    client = client or httpx.Client()
    try:
        try:
            r = client.get(url, headers={"User-Agent": TG_UA}, timeout=30, follow_redirects=True)
        except Exception as e:
            print(f"[tg:{channel}] 请求异常：{e}", file=sys.stderr)
            return []
        h = r.text
        extra = re.search(r'class="tgme_page_extra"[^>]*>(.*?)</div>', h, re.S)
        extra_txt = re.sub(r'<[^>]+>', '', extra.group(1)).strip() if extra else ""

        out = []
        for p in re.split(r'(?=<div class="tgme_widget_message[ "])', h):
            dp = re.search(r'data-post="([^"]+)"', p)
            if not dp:
                continue
            post = dp.group(1)  # e.g. bananagunannouncements/445
            tm = re.search(r'<time[^>]*datetime="([^"]+)"', p)
            ts = tm.group(1) if tm else ""
            blocks = re.findall(
                r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*'
                r'(?:<div class="tgme_widget_message_(?:footer|reply|forwarded|meta)|'
                r'<a class="tgme_widget_message_date)', p, re.S)
            text = _strip_html(" ".join(blocks))
            if not text:
                continue
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
            except Exception:
                when = None
            if when and when < cutoff_dt:
                continue
            out.append({"text": text, "url": f"https://t.me/{post}", "ts": ts, "msg_id": post})

        if not out:
            if "member" in extra_txt.lower():
                print(f"[tg:{channel}] 看起来是『群』不是广播频道（{extra_txt[:40]}），"
                      f"读不到帖子流。", file=sys.stderr)
            else:
                print(f"[tg:{channel}] 未解析到帖子（可能是验证门/落地页，或近窗口无更新）。",
                      file=sys.stderr)
        return out
    finally:
        if owns:
            client.close()


# ---------------- Discord 频道 ----------------
def _discord_msg_text(m):
    """正文 + 各 embed 的作者/标题/描述/字段。被 Follow 同步来的公告常在 embeds 里。"""
    parts = []
    c = (m.get("content") or "").strip()
    if c:
        parts.append(c)
    for emb in (m.get("embeds") or []):
        au = emb.get("author")
        if isinstance(au, dict) and (au.get("name") or "").strip():
            parts.append(au["name"].strip())
        for k in ("title", "description"):
            v = (emb.get(k) or "").strip()
            if v:
                parts.append(v)
        for f in (emb.get("fields") or []):
            n = (f.get("name") or "").strip()
            val = (f.get("value") or "").strip()
            seg = (n + "：" + val).strip("：")
            if seg:
                parts.append(seg)
    return "\n".join(parts).strip()


def _discord_msg_url(m):
    u = _first_url(_discord_msg_text(m))
    if u:
        return u
    for emb in (m.get("embeds") or []):
        if emb.get("url"):
            return emb["url"]
    return ""


def read_discord_channel(token, channel_id, cutoff_dt, client=None):
    """用 Bot token 读某频道近 cutoff 之后的消息。返回 [{text,url,ts,msg_id}]。"""
    if not token or not channel_id:
        return []
    owns = client is None
    client = client or httpx.Client()
    headers = {"Authorization": f"Bot {token}",
               "User-Agent": "DiscordBot (gmgn-competitor-radar, 1.0)"}
    try:
        try:
            r = client.get(f"{DISCORD_API}/channels/{channel_id}/messages",
                           headers=headers, params={"limit": 100}, timeout=30)
        except Exception as e:
            print(f"[discord:{channel_id}] 异常：{e}", file=sys.stderr)
            return []
        if r.status_code != 200:
            print(f"[discord:{channel_id}] 读取失败 {r.status_code}: {r.text[:150]} —— "
                  f"检查 bot 是否在服务器/频道、Token 是否有效、是否开了 MESSAGE CONTENT INTENT",
                  file=sys.stderr)
            return []
        msgs = r.json()
        out = []
        for m in msgs:
            # 只要真消息（0=普通，19=回复）；过滤系统消息（如 12=关注确认 CHANNEL_FOLLOW_ADD 等）
            if m.get("type") not in (0, 19):
                continue
            ts = m.get("timestamp")
            if not ts:
                continue
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if when < cutoff_dt:
                continue
            text = _discord_msg_text(m)
            if not text:
                continue
            out.append({"text": text, "url": _discord_msg_url(m),
                        "ts": ts, "msg_id": str(m.get("id", ""))})
        return out
    finally:
        if owns:
            client.close()


# ---------------- 网站前端 JS 公告（写死在前端的弹窗/公告）----------------
# 适用于「公告写死在前端代码里、没有独立接口」的竞品（如 DeBot）。
# 抓首页→定位当前主 JS（文件名带 hash 会变，所以每次动态解析）→下载→按规则提取公告。
# 每个站点的提取规则不同，用 WEB_JS_RULES 配置。
# mode="single_js"：公告在单一主JS里（DeBot）
# mode="nextjs_chunks"：Next.js 应用，公告在某个 chunk 里（BasedBot）
WEB_JS_RULES = {
    "debot": {
        "mode": "single_js",
        "base": "https://debot.ai",
        "js_pattern": r'src="(/assets/[^"]*index-[^"]*\.js)"',
        "title_pattern": r'title:\{zh:"([^"]{2,80})"',
        "desc_pattern": r'desc:\{zh:"([^"]{0,200})"',
    },
    "basedbot": {
        "mode": "nextjs_chunks",
        "base": "https://basedbot.app",
        "marker": "changelogEntries",
        "block_pattern": r'changelogEntries:\[(\{title:"[^"]*",description:"[^"]*"\}(?:,\{title:"[^"]*",description:"[^"]*"\})*)\]',
        "entry_pattern": r'\{title:"([^"]*)",description:"([^"]*)"\}',
    },
}


def _webjs_single(rule, site_key, client):
    base = rule["base"]
    r = client.get(base + "/", headers={"User-Agent": TG_UA}, timeout=30, follow_redirects=True)
    m = re.search(rule["js_pattern"], r.text)
    if not m:
        print(f"[web_js:{site_key}] 未在首页找到主 JS", file=sys.stderr)
        return []
    js_url = base + m.group(1)
    jr = client.get(js_url, headers={"User-Agent": TG_UA}, timeout=30, follow_redirects=True)
    js = jr.text
    items = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for tm in re.finditer(rule["title_pattern"], js):
        title = tm.group(1).strip()
        tail = js[tm.end():tm.end() + 600]
        dm = re.search(rule["desc_pattern"], tail)
        desc = (dm.group(1).strip() if dm else "")
        text = f"{title}\n{desc}" if desc else title
        mid = f"webjs:{site_key}:" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
        items.append({"text": text, "url": js_url, "ts": today, "msg_id": mid})
    return items


def _webjs_nextjs(rule, site_key, client):
    base = rule["base"]
    r = client.get(base + "/", headers={"User-Agent": TG_UA}, timeout=30, follow_redirects=True)
    chunks = sorted(set(re.findall(r'/_next/static/chunks/[^"]*\.js', r.text)))
    if not chunks:
        print(f"[web_js:{site_key}] 首页未找到 Next.js chunk", file=sys.stderr)
        return []
    marker = rule["marker"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = []
    for c in chunks:
        try:
            cr = client.get(base + c, headers={"User-Agent": TG_UA}, timeout=15, follow_redirects=True)
        except Exception:
            continue
        js = cr.text
        if marker not in js:
            continue
        for bm in re.finditer(rule["block_pattern"], js):
            entries = re.findall(rule["entry_pattern"], bm.group(1))
            if entries and re.search(r'[\u4e00-\u9fa5]', entries[0][0] + entries[0][1]):
                for title, desc in entries:
                    title, desc = title.strip(), desc.strip()
                    text = f"{title}\n{desc}" if desc else title
                    mid = f"webjs:{site_key}:" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
                    items.append({"text": text, "url": base + c, "ts": today, "msg_id": mid})
                break
        if items:
            break
    if not items:
        print(f"[web_js:{site_key}] 遍历 {len(chunks)} 个 chunk 未提取到公告", file=sys.stderr)
    return items


def read_web_js(site_key, cutoff_dt=None, client=None):
    """按 WEB_JS_RULES[site_key] 抓竞品网站前端 JS，提取公告。"""
    rule = WEB_JS_RULES.get(site_key)
    if not rule:
        print(f"[web_js:{site_key}] 未配置提取规则", file=sys.stderr)
        return []
    owns = client is None
    client = client or httpx.Client()
    try:
        mode = rule.get("mode", "single_js")
        if mode == "nextjs_chunks":
            return _webjs_nextjs(rule, site_key, client)
        return _webjs_single(rule, site_key, client)
    except Exception as e:
        print(f"[web_js:{site_key}] 提取异常：{e}", file=sys.stderr)
        return []
    finally:
        if owns:
            client.close()
