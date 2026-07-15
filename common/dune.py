# -*- coding: utf-8 -*-
"""Dune 接入：跑一个已保存的 query，取结果。

- 所有请求带 429/5xx 退避重试（尊重 Retry-After），避免免费版限流打断。
- get_query_result(query_id, refresh=False)
    refresh=False -> 优先返回 Dune 已缓存的最新结果（不重跑、不花额度）；无缓存才执行。
    refresh=True  -> 强制重新执行，拿当天最新结果（会花额度）。
"""
import os
import time
import httpx

BASE = "https://api.dune.com/api/v1"


def _headers():
    key = os.environ.get("DUNE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 DUNE_API_KEY")
    return {"X-Dune-Api-Key": key}


def _req(client, method, url, **kw):
    """带 429/5xx 退避重试的请求。"""
    last = None
    for attempt in range(7):
        r = client.request(method, url, headers=_headers(), **kw)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After") or 0) or min(2 ** attempt, 30)
            time.sleep(wait)
            last = r
            continue
        r.raise_for_status()
        return r
    if last is not None:
        last.raise_for_status()
    return last


def _execute_and_wait(client, query_id, max_wait):
    ex = _req(client, "POST", f"{BASE}/query/{query_id}/execute")
    eid = ex.json()["execution_id"]
    waited = 0
    cost = None
    while waited < max_wait:
        st = _req(client, "GET", f"{BASE}/execution/{eid}/status")
        j = st.json()
        state = j.get("state")
        if state == "QUERY_STATE_COMPLETED":
            cost = j.get("execution_cost_credits")
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(f"Dune 执行失败：{state}")
        time.sleep(5)
        waited += 5
    if cost is not None:
        print(f"    Dune query {query_id} 本次消耗 {cost} credits")
    res = _req(client, "GET", f"{BASE}/execution/{eid}/results", params={"limit": 200})
    return (res.json().get("result") or {}).get("rows") or []


def get_query_result(query_id, max_wait=240, refresh=False):
    with httpx.Client(timeout=60) as client:
        if not refresh:
            try:
                r = _req(client, "GET", f"{BASE}/query/{query_id}/results",
                         params={"limit": 200})
                rows = (r.json().get("result") or {}).get("rows")
                if rows:
                    return rows
            except Exception:
                pass  # 无缓存/取缓存失败 -> 往下执行
        return _execute_and_wait(client, query_id, max_wait)
