#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品晚报 → Slack（功能更新 + 官方推特高优/创始人回复 + 高热话题 + 交易数据）
读 3 个自有 store + 抓同事「官方推特监控」页(2298085418)解析。只读、不写台账。
环境变量：SLACK_BOT_TOKEN+SLACK_CHANNEL / ATLASSIAN_EMAIL+ATLASSIAN_API_TOKEN(抓同事页)
         / NIGHTLY_DRY_RUN=1 / NIGHTLY_TARGET_DATE=YYYY-MM-DD"""
import os, re, sys, json, pathlib, html as htmllib
from difflib import SequenceMatcher
from collections import defaultdict, OrderedDict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common.util import load_dotenv, enable_truststore, load_config, today_cst
from common.slack import (slack_from_env, post_blocks, section, divider, context,
                          link, mrkdwn_escape, pack_units, cap_message_blocks)

CLOUD = "pionex.atlassian.net"
FEATURE_FILE, XTOPICS_FILE, TRADING_FILE = "data/feature_updates.json", "data/x_topics.json", "data/trading_data.json"
OFFICIAL_PID = "2298085418"
SPACE = "~712020158205a1add2439fae73253b196308bc"
def _wiki(pid): return f"https://{CLOUD}/wiki/spaces/{SPACE}/pages/{pid}"
FEATURE_URL, XTOPICS_URL, OFFICIAL_URL, TRADING_URL = _wiki("2303656153"), _wiki("2303656137"), _wiki(OFFICIAL_PID), _wiki("2303721623")
SENT_EMOJI = {"正面": "•", "负面": "•", "中性": "•"}
HI_MAX, FOUNDER_MAX, TOPIC_MAX, SIM_TH = 5, 2, 5, 0.55

def _load_json(p, d):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return d
def _norm(s):
    s = (s or "").lower().strip(); s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)
    return re.sub(r"[\s\u3000._\-]", "", s)
def _core_set():
    fu = load_config().get("feature_updates", {}) or {}
    return {_norm(x.get("label","")) for x in (fu.get("directory",[]) or []) if x.get("tier")=="core"}
def _clip(s, n):
    s = (s or "").strip(); return s if len(s) <= n else s[:n].rstrip() + "…"
def _usd(n):
    try: n = float(n)
    except Exception: return "-"
    if n>=1e9: return f"${n/1e9:.1f}B"
    if n>=1e6: return f"${n/1e6:.0f}M"
    if n>=1e3: return f"${n/1e3:.0f}K"
    return f"${n:.0f}"
def _wan(n):
    try: n = float(n)
    except Exception: return "-"
    return f"{n/1e4:.1f}万" if n>=1e4 else f"{int(n)}"
def _sid(u):
    m = re.search(r"/status/(\d+)", u or ""); return m.group(1) if m else ""

def _nt(t): return re.sub(r"[，。、,.\s（）()]", "", t or "")
def _sim(a, b):
    a, b = _nt(a), _nt(b)
    if not a or not b: return 0.0
    if a in b or b in a: return 0.95
    return SequenceMatcher(None, a, b).ratio()
def _merge(topics):
    bysub = defaultdict(list)
    for t in topics: bysub[t.get("subject")].append(t)
    out = []
    for sub, items in bysub.items():
        clusters = []
        for it in items:
            for cl in clusters:
                if _sim(it.get("topic"), cl[0].get("topic")) >= SIM_TH: cl.append(it); break
            else: clusters.append([it])
        best = max(clusters, key=len)
        rep = dict(max(best, key=lambda x: len(x.get("topic",""))))
        ex = []
        for x in best: ex.extend(x.get("examples",[]) or [])
        rep["examples"] = list(dict.fromkeys(ex)); rep["_merged"] = len(best)
        out.append(rep)
    return out

def _fetch_storage(pid):
    email, token = os.environ.get("ATLASSIAN_EMAIL"), os.environ.get("ATLASSIAN_API_TOKEN")
    if not (email and token): return None
    try:
        import httpx
        r = httpx.get(f"https://{CLOUD}/wiki/api/v2/pages/{pid}?body-format=storage", auth=(email, token), timeout=30)
        r.raise_for_status(); return r.json()["body"]["storage"]["value"]
    except Exception as e:
        print(f"[官方推特] 抓取失败：{e}", file=sys.stderr); return None
def _detag(s):
    s = re.sub(r"<br\s*/?>", " ", s or ""); s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s).strip()
def _p_title(c):
    m = re.search(r'<ac:parameter ac:name="title">([^<]+)</ac:parameter>', c or "")
    if m: return m.group(1).strip()
    m = re.search(r'data-type="status"[^>]*>([^<]+)<', c or "")
    return m.group(1).strip() if m else _detag(c)
def _p_date(c):
    m = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', c or "")
    if m: return m.group(1)
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', c or "")
    if m: mo,da,yr = m.groups(); return f"{yr}-{int(mo):02d}-{int(da):02d}"
    return ""
def _p_url(c):
    m = re.search(r'href="(https?://[^"]*?/status/\d+)"', c or "")
    if m: return m.group(1)
    m = re.search(r'href="(https?://[^"]+)"', c or ""); return m.group(1) if m else ""
def parse_official(storage):
    out = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", storage or "", re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 7: continue
        out.append({"date": _p_date(cells[0]), "comp": _detag(cells[1]).lower(),
                    "account": _detag(cells[2]), "dyn": _detag(cells[3]),
                    "pr": _p_title(cells[5]), "url": _p_url(cells[6])})
    return out
def _acc(a): return (a or "").split("@")[0].strip() or a
def _clean(s):
    s = (s or "").strip()
    m = re.match(r"^.{0,6}?(回复|引用|转发)\s*[｜|]\s*(.+)$", s, re.S)
    return m.group(2).strip() if m else s

def block_feature(target, core):
    store = _load_json(FEATURE_FILE, [])
    def bad(it):
        s=(it.get("summary") or "").strip(); t=(it.get("title") or "").strip()
        if re.match(r"^(Yellow|Red|Grey|Green|Blue|Purple)\s*[高中低]?$", s): return True
        if re.match(r"^(Yellow|Red|Grey|Green|Blue|Purple)\s*[高中低]?$", t): return True
        return len(s) < 4
    todays = [it for it in store if it.get("date")==target and _norm(it.get("competitor",""))in core and not bad(it)]
    rank = {"高优":0,"高":0,"中优":1,"中":1,"低优":2,"低":2}
    by = OrderedDict()
    for it in todays: by.setdefault(it.get("competitor"), []).append(it)
    units = []
    for comp, items in by.items():
        items.sort(key=lambda x: (rank.get(x.get("priority",""),3), 0 if x.get("url") else 1))
        it = items[0]
        line = f"• *{mrkdwn_escape(comp or '—')}* {mrkdwn_escape(_clip(it.get('title'),46))}"
        if it.get("url"): line += f"　{link(it['url'],'原文')}"
        units.append(line)
    return "📢 *重点功能更新*", units

def block_official(target):
    storage = _fetch_storage(OFFICIAL_PID)
    if storage is None: return "🐦 *官方推特高优 / 创始人*", None
    rows = parse_official(storage)
    feat_ids = {s for it in _load_json(FEATURE_FILE, []) if (s:=_sid(it.get("url","")))}
    hi = [r for r in rows if r["pr"]=="高" and r["comp"]!="gmgn" and r["date"]==target and _sid(r["url"]) not in feat_ids][:HI_MAX]
    fdr = [r for r in rows if r["pr"]=="中" and r["comp"]!="gmgn" and r["date"]==target
           and ("创始人" in r["account"] or "联创" in r["account"]) and "回复" in r["dyn"]][:FOUNDER_MAX]
    units = []
    for r in hi + fdr:
        line = f"• *{mrkdwn_escape(_acc(r['account']))}* {mrkdwn_escape(_clip(_clean(r['dyn']),54))}"
        if r["url"]: line += f"　{link(r['url'],'原文')}"
        units.append(line)
    return "🐦 *官方推特高优 / 创始人*", units

def block_topic(target, core):
    hi = [it for it in _load_json(XTOPICS_FILE, []) if it.get("date")==target and it.get("heat")=="高"]
    merged = _merge(hi)
    merged.sort(key=lambda x: (_norm(x.get("subject"))!="gmgn", _norm(x.get("subject")) not in core, -x.get("_merged",1)))
    units = []
    for it in merged[:TOPIC_MAX]:
        emo = SENT_EMOJI.get(it.get("sentiment"),"💬")
        line = f"{emo} *{mrkdwn_escape(it.get('subject') or '—')}* {mrkdwn_escape(_clip(it.get('topic'),48))}"
        exs = it.get("examples",[]) or []
        if exs: line += f"　{link(exs[0],'原推')}"
        units.append(line)
    return "📣 *高热话题*", units

def block_trading():
    d = _load_json(TRADING_FILE, {}); items = d.get("items",[]) or []; users = d.get("users",[]) or []
    def is_g(x): return "gmgn" in _norm(x.get("label",""))
    lines = []
    volr = sorted([it for it in items if (it.get("vol") or {}).get("d30") is not None], key=lambda it: it["vol"]["d30"], reverse=True)
    if volr:
        gi = next((i for i,it in enumerate(volr) if is_g(it)), None); top = volr[0]
        if gi is not None:
            g = volr[gi]; lines.append(f"• 交易量 GMGN *#{gi+1}/{len(volr)}* · 30d {_usd(g['vol']['d30'])} · 24h {_usd(g['vol'].get('d1'))}")
        lines.append(f"• 龙头 *{mrkdwn_escape(top.get('label'))}* 30d {_usd(top['vol']['d30'])}")
        cand = [it for it in volr[:12] if not is_g(it) and (it.get('vol') or {}).get('d7')]
        def surge(it):
            d1=it['vol'].get('d1') or 0; wk=(it['vol']['d7'] or 0)/7.0; return d1/wk if wk>0 else 0
        if cand:
            mv = max(cand, key=surge); r = surge(mv)
            if r>=1.25: lines.append(f"• 异动 *{mrkdwn_escape(mv.get('label'))}* 24h放量 {_usd(mv['vol'].get('d1'))}（周均 {_usd((mv['vol']['d7'] or 0)/7.0)}，约 {r:.1f}×）")
    usr = sorted([u for u in users if u.get("users_30d") is not None], key=lambda u: u["users_30d"], reverse=True)
    if usr:
        gi = next((i for i,u in enumerate(usr) if is_g(u)), None)
        if gi is not None:
            g = usr[gi]; lines.append(f"• 活跃用户 GMGN *#{gi+1}/{len(usr)}* · 30d {_wan(g['users_30d'])} · 今日 {_wan(g.get('users_today'))}")
    return "📊 *交易数据*", lines

def _emit(blocks, title, units):
    blocks.append(section(title))
    if units is None: blocks.append(context("_未取到官方推特页（检查 ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN）_"))
    elif not units: blocks.append(context("_今日无_"))
    else: blocks.extend(pack_units(units))
def _plain(u): return re.sub(r"<[^|>]+\|([^>]+)>", r"[\1]", u).replace("*","")

def main():
    load_dotenv(); enable_truststore()
    dry = os.environ.get("NIGHTLY_DRY_RUN","0")=="1"
    slack = slack_from_env()
    if not slack and not dry: print("未配置 Slack —— 跳过。"); return
    target = os.environ.get("NIGHTLY_TARGET_DATE","").strip() or today_cst()
    core = _core_set()
    b = [block_feature(target, core), block_official(target), block_topic(target, core), block_trading()]
    if dry:
        print(f"===== DRY_RUN 竞品晚报 {target} =====\n")
        for title, units in b:
            print("■", _plain(title))
            if units is None: print("   （未取到官方推特页）")
            elif not units: print("   今日无")
            else:
                for u in units: print("  ", _plain(u))
            print()
        return
    blocks = [section(f"🌙 *竞品晚报* · {target}"), divider()]
    for i,(title,units) in enumerate(b):
        _emit(blocks, title, units)
        blocks.append(divider())
    blocks.append(context(f"📋 看板　{link(FEATURE_URL,'功能更新')}　·　{link(XTOPICS_URL,'X话题')}　·　{link(OFFICIAL_URL,'官方推特')}　·　{link(TRADING_URL,'交易数据')}"))
    blocks = cap_message_blocks(blocks, FEATURE_URL)
    if post_blocks(slack, blocks, f"竞品晚报 {target}"):
        print(f"✓ 已发送 竞品晚报（{target}）")

if __name__ == "__main__":
    main()
