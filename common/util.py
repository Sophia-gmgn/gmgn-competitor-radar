# -*- coding: utf-8 -*-
"""共用小工具：加载 .env / config、底稿读写与去重合并、日期排序。"""
import os
import json
import pathlib
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))  # 北京时间


def load_dotenv(path=None):
    """本地跑时读 .env（上 GitHub Actions 用 Secrets，不需要本文件）。"""
    p = pathlib.Path(path or ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def enable_truststore():
    """用系统钥匙串验证证书（解决公司网络自签证书；云端无影响）。"""
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception as e:
        print(f"[truststore] 未启用（{e}）")


def load_config(path="config.yaml"):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def now_cst_str():
    return datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M")


def today_cst():
    return datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d")


def by_date_desc(items):
    return sorted(items, key=lambda x: (x.get("date", ""), x.get("_id", "")), reverse=True)


def load_store(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def save_store(path, items):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_by_id(store, new_items):
    """按 _id 去重合并，返回新增条数（保留历史，只 append 新的）。"""
    seen = {it.get("_id") for it in store}
    added = 0
    for it in new_items:
        _id = it.get("_id")
        if _id and _id not in seen:
            store.append(it)
            seen.add(_id)
            added += 1
    return added
