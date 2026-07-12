import os
k = os.environ.get("DUNE_API_KEY", "")
print(f"DUNE_API_KEY 读到长度: {len(k)}")
print(f"前4位: {k[:4]!r}  后4位: {k[-4:]!r}")
if not k:
    print(">>> 结论: GitHub 传进来是空的 —— secret 值本身有问题")
elif len(k) < 20:
    print(">>> 结论: key 太短，不是有效 key（可能粘错）")
else:
    print(">>> 结论: key 长度正常，尝试调用 Dune...")
    import httpx
    r = httpx.get("https://api.dune.com/api/v1/query/7954296/results",
                  headers={"X-Dune-Api-Key": k}, params={"limit": 1}, timeout=30)
    print(f"Dune 返回状态码: {r.status_code}")
    print(f"Dune 返回前200字: {r.text[:200]}")
