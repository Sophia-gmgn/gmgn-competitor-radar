# -*- coding: utf-8 -*-
"""
xAI (Grok) 封装 —— 改自 Cynthia 的 grok_fetch。
Responses API + x_search 工具：让 Grok 边搜边判、直接吐结构化 JSON。
"""
import os
import re
import json

import httpx

XAI_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = os.environ.get("GROK_MODEL", "grok-4.3").strip()


def extract_output_text(data):
    """从 Responses API 返回里取正文文本。"""
    parts = []
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text" and c.get("text"):
                    parts.append(c["text"])
    if not parts and isinstance(data.get("output_text"), str):
        parts.append(data["output_text"])
    return "\n".join(parts).strip()


def parse_json_array(text):
    """把模型返回文本尽量解析成 JSON 数组（去 ```json 包裹、去行内引用标记、截取 [ ... ]）。"""
    if not text:
        return []
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"```$", "", t).strip()
    t = re.sub(r"\[\[\d+\]\]\([^)]*\)", "", t)
    s, e = t.find("["), t.rfind("]")
    if s == -1 or e == -1 or e < s:
        return []
    try:
        parsed = json.loads(t[s:e + 1])
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def x_search_tool(from_date, to_date, allowed_handles=None):
    """构造 x_search 工具配置。
    - 不传 allowed_handles = 全网广搜（本项目 X 话题日报用）。
    - 传了 = 只看这些账号（最多 20 个）。
    """
    tool = {"type": "x_search", "from_date": from_date, "to_date": to_date}
    if allowed_handles:
        tool["allowed_x_handles"] = list(allowed_handles)
    return tool


def call_grok(client, api_key, prompt, tools=None, model=None, timeout=180):
    """发一次 Grok 调用，返回 {ok, text, items, citations, usage, err}。"""
    body = {"model": (model or DEFAULT_MODEL),
            "input": [{"role": "user", "content": prompt}]}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        r = client.post(XAI_URL, headers=headers, json=body, timeout=timeout)
    except Exception as e:
        return {"ok": False, "err": f"请求异常：{e}", "text": "",
                "items": [], "citations": [], "usage": {}}
    if r.status_code != 200:
        return {"ok": False, "err": f"HTTP {r.status_code}: {r.text[:300]}", "text": "",
                "items": [], "citations": [], "usage": {}}
    data = r.json()
    text = extract_output_text(data)
    return {"ok": True, "text": text, "items": parse_json_array(text),
            "citations": data.get("citations") or [], "usage": data.get("usage") or {}}
