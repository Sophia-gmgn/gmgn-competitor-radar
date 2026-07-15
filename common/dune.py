# -*- coding: utf-8 -*-
"""Dune 接入：跑一个已保存的 query，取结果。

get_query_result(query_id, refresh=False)
  refresh=False -> 优先返回 Dune 上已缓存的最新结果（不重跑、不花额度）；无缓存才执行。
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


def _execute_and_wait(client, query_id, max_wait):
    ex = client.post(f"{BASE}/query/{query_id}/execute", headers=_headers())
    ex.raise_for_status()
    eid = ex.json()["execution_id"]
    waited = 0
    while waited < max_wait:
        st = client.get(f"{BASE}/execution/{eid}/status", headers=_headers())
        st.raise_for_status()
        state = st.json().get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(f"Dune 执行失败：{state}")
        time.sleep(3)
        waited += 3
    res = client.get(f"{BASE}/execution/{eid}/results", headers=_headers(),
                     params={"limit": 200})
    res.raise_for_status()
    return (res.json().get("result") or {}).get("rows") or []


def get_query_result(query_id, max_wait=180, refresh=False):
    with httpx.Client(timeout=60) as client:
        if not refresh:
            r = client.get(f"{BASE}/query/{query_id}/results", headers=_headers(),
                           params={"limit": 200})
            if r.status_code == 200:
                rows = (r.json().get("result") or {}).get("rows")
                if rows:
                    return rows
        return _execute_and_wait(client, query_id, max_wait)
