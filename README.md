# gmgn-competitor-radar

GMGN 竞品情报监控 · **新需求扩展库**（跟 Cynthia 的生产库 `gmgn-x-monitor` 分开维护，互不影响；验证 OK 再合并回去）。

复用 `gmgn-x-monitor` 的做法：Grok 一次调用连抓带判（xAI Responses API + `x_search`）、数据落本地 JSON、幂等写 Confluence、Slack 播报。每个功能一个模块、一个 workflow，共用 `common/`。

> 当前进度：**Phase 1 — X 话题日报（#1 · Haze）** 已就位。后续 Phase（功能更新频道 / 链上动作 / 交易数据…）按 `开发计划` 陆续加。

---

## 目录结构

```
common/            # 共用件（从 gmgn-x-monitor 抽出并泛化）
  xai.py           #   Grok Responses API + x_search 封装
  confluence.py    #   Confluence REST v2 客户端 + 渲染 helper
  slack.py         #   Slack Block Kit 构造 + 发送 + 状态
  util.py          #   .env / config / 底稿读写去重 / 日期
x_topics/          # #1 X 话题日报
  fetch.py         #   广搜“GMGN + 竞品”提及、Grok 归纳当天话题、过滤邀请链接帖
  to_confluence.py #   写「X 话题日报」页
  to_slack.py      #   发同频道日报（🌐 X 话题日报）
data/  state/      # 底稿与状态（由 workflow 提交回仓库）
.github/workflows/ # x-topics-test（手动测试）+ x-topics（每日）
config.yaml  .env.example  requirements.txt
```

---

## 需要人工配置的密钥 / 值（占位待填）

**上 GitHub Actions**：仓库 **Settings → Secrets and variables → Actions → New repository secret**，加下面这些同名 Secret：

| Secret 名 | 用途 | Phase |
|---|---|---|
| `XAI_API_KEY` | Grok 抓取 + 研判 | 1 |
| `ATLASSIAN_EMAIL` | 写 Confluence（登录邮箱） | 1 |
| `ATLASSIAN_API_TOKEN` | 写 Confluence（API Token） | 1 |
| `X_TOPICS_PAGE_ID` | 「X 话题日报」页的 pageId | 1（建好页后） |
| `SLACK_BOT_TOKEN` | 发 Slack（xoxb-…） | 1 |
| `DISCORD_BOT_TOKEN` | 读 Discord 公告频道 | 2 |

非密钥的固定值（频道 ID、页面网址）已写在 workflow 里，可直接改：
- `SLACK_CHANNEL`：`C0BGRHXM133`（和 Cynthia 同一个竞品监控频道）
- `X_TOPICS_PAGE_URL`：建好页后填页面网址（日报末尾跳转，可选）

**本地跑**：把 `.env.example` 复制成 `.env` 填真实值即可（`.env` 不进仓库）。

---

## 上线步骤（建议 先验证再接线）

1. **配 `XAI_API_KEY`**（先只配这一个就能测抓取）。
2. **跑测试**：Actions → **x-topics-test** → Run workflow。跑完看日志里各主体抓到几个话题、**引用源合计**（≈搜索用量，费用主要看这个）、话题质量；下载 `x-topics-result` artifact 看结构化结果。
   - 噪音多 / 抓串了 → 改 `config.yaml` 里对应主体的 `terms`（收窄）。
   - 漏了 → 放宽 `terms`。
3. **建 Confluence 页**：在「竞品情报监控（总入口）」下的「X 话题日报」页，拿它的 **pageId**，配成 `X_TOPICS_PAGE_ID`；可选把页面网址配成 workflow 里的 `X_TOPICS_PAGE_URL`。
4. **配 `ATLASSIAN_*` + `SLACK_BOT_TOKEN`**。
5. **启用每日**：**x-topics** workflow 每天北京 20:30 自动跑（也可手动 Run 一次验证）。跑通后会：写「X 话题日报」页 + 发一条 `🌐 X 话题日报` 到频道。

> 首次接 Slack 想先看版式：手动 Run 一次 x-topics 即可（当天首次会发；同日重复会自动跳过，测试时可在该步临时加 `X_TOPICS_FORCE_POST: "1"`）。

---

## 顺带：把 basedbot 补进 Cynthia 的账号发帖监控

本库的话题扫描已含 basedbot（`@BasedBot`）。但 basedbot 作为「**账号发帖**监控」要加到 **`gmgn-x-monitor`** 那边，三处（先确认 `@BasedBot` 是官方号）：

1. `grok_fetch.py` 的 `ACCOUNTS` 加一行（放“交易终端竞品”那段）：
   ```python
   ("BasedBot", "BasedBot 官方", "basedbot"),
   ```
2. `grok_confluence.py` 的 `CATEGORIES` 里 `"meme"` 集合加 `"basedbot"`（否则归到“其他”区）。
3. `grok_slack.py` 的 `CATEGORIES` 里 `"meme"` 集合同样加 `"basedbot"`。

这三处动的是 Cynthia 的生产库，跟她对一下再改。

---

## 说明

- **只抓公开内容**：公开推文 / 公开讨论；不抓私信、受保护账号。
- **广搜行为需实测**：`x_search` 不限 handle 的广搜覆盖度和费用要以 x-topics-test 的实跑结果为准；据此调 `terms`。
- 这是**你自己的库**，随便迭代；验证好的模块之后可合并回 `gmgn-x-monitor`。

---

# 功能② 竞品功能更新（#2 + #4 + #3）

监控竞品官方渠道发布的**功能更新**（新功能/优化/集成），归纳后写「竞品功能更新」页。

## 读取源（两种，config 里加一行即可扩展）
- **公开 TG 广播频道**：读 `t.me/s/<频道名>`，**无需 bot**。认准是“频道(subscribers)”而非“群(members)”或验证门。
- **Discord 频道**：读**你自己服务器里 Follow 了竞品公告频道**的那个频道（需 `DISCORD_BOT_TOKEN`）。
  - 做法：你建个自己的 Discord 服务器 → 在竞品的公告频道点「关注/Follow」转发进你的频道 → 建个 bot 拉进你服务器（开 MESSAGE CONTENT INTENT）→ 把**你那个频道的 ID** 填进 config。

当前已配：DeBot（Discord 频道 `1525442051876061295`）、Banana Gun（公开 TG `bananagunannouncements`）。

## 需要的 Secret（在原有基础上加这些）
| Secret | 用途 |
|---|---|
| `DISCORD_BOT_TOKEN` | 读你服务器里 Follow 竞品公告的频道 |
| `FEATURE_UPDATES_PAGE_ID` | 「竞品功能更新」页的 pageId |

（`XAI_API_KEY`、`ATLASSIAN_*` 与功能①共用，无需重配。）

## 上线步骤
1. 配 `DISCORD_BOT_TOKEN` + `FEATURE_UPDATES_PAGE_ID`。
2. **先测**：Actions → **feature-updates-test** → Run。看各竞品读到几条原文、归纳出几条功能更新；下载 `feature-updates-result` 看质量。
3. **启用**：**feature-updates** 每天北京 10:00 / 22:00 自动跑（写页面 + Slack 自动跳过）。

## 扩展竞品
在 `config.yaml` 的 `feature_updates.competitors` 加一条，给 `telegram_channel`（公开频道）或 `discord_channel_id`（你 Follow 的频道）。找不到可读源的（Photon/Trojan/Moby 等）先不加。
