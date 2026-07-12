# -*- coding: utf-8 -*-
"""Dune 接入：跑一个已保存的 query，取最新结果。"""
import os
import time
import httpx

BASE = "https://api.dune.com/api/v1"


def _headers():
    key = os.environ.get("DUNE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 DUNE_API_KEY")
    return {"X-Dune-Api-Key": key}


def get_query_result(query_id, max_wait=120):
    with httpx.Client(timeout=60) as client:
        r = client.get(f"{BASE}/query/{query_id}/results", headers=_headers(),
                       params={"limit": 200})
        if r.status_code == 200:
            rows = (r.json().get("result") or {}).get("rows")
            if rows:
                return rows
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
