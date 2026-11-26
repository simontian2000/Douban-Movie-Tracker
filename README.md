# Douban-Movie-Tracker

通过豆瓣 RSS 跟踪电影标记动态，并将新增记录推送到 Discord 的轻量 Python 工具。支持“想看”“在看”“看过”状态，以 Discord Embed 展示电影名称、原始链接、标记日期，以及 RSS 中能够提取到的个人评分、短评和海报。

程序每次执行一轮检查后退出，仓库使用 GitHub Actions 定时运行。实现以 [sync.py](sync.py) 和 [工作流](.github/workflows/sync.yml)为准。

## 功能

- 从 RSS 中筛选链接包含 `movie.douban.com/subject/`、标题以“看过”“想看”或“在看”开头的记录。
- 将状态分别显示为“已看过”“想看”“正在看”。
- 从“推荐：”或“评分：”后的评价词提取个人评分：“力荐”5 星、“推荐”4 星、“还行”3 星、“较差”2 星、“很差”1 星。
- 提取“备注：”后的短评，遇后续评分、推荐或标签行截断；推送时最多保留短评前 3500 个字符。
- 尝试下载 RSS 内容中的第一张图片，作为海报附件；失败、非图片或超过 8 MiB 时，发送不带海报的卡片。
- 使用本地 JSON 记录已处理标识，避免正常运行时重复推送。
- 首次初始化只建立历史基线，不推送当前 RSS 中已有记录。

这是 RSS 增量通知工具，不提供豆瓣登录、Cookie 配置、历史全量抓取、评分统计、网页界面或双向同步。只处理 RSS 返回的内容；两次运行之间已经从订阅源移出的记录可能无法捕获。

## 工作流程

```text
定时或手动启动
  → feedparser 读取 DOUBAN_RSS_URL
  → 筛选电影条目并提取状态、日期、评分、短评与海报
  → 读取 data/sent_records.json
      ├─ 尚未初始化：记录本轮条目标识，保存后退出
      └─ 已初始化：筛出未记录条目，反转 RSS 顺序后逐条发送
          → 下载海报（失败则不带海报）
          → POST 到 Discord Webhook
          → 成功后立即保存该条记录
  → Actions 提交并推送 data/ 中的变化
```

RSS 通常倒序排列，因此代码通过反转列表尝试先发旧记录，未进行独立的日期排序。

去重标识为以下字符串的 SHA-256：

```text
条目链接|转换后的状态|标记日期
```

日期来自 RSS 的 `published`，优先解析为 `YYYY-MM-DD`；解析失败时取原字符串前 10 个字符，缺失则为空。日期未统一转换到某个时区。同一链接、同一状态、同一天的评分或短评变化不会产生新通知；状态或日期改变可能产生新通知。该机制不保证异常情况下严格只发送一次。

## 环境要求

- Python 3.12（Actions 使用的版本）。
- 可访问豆瓣 RSS、海报地址和 Discord 的网络。
- 可用且返回预期动态格式的豆瓣 RSS 地址。
- 目标 Discord 频道的 Webhook URL。
- 使用 GitHub Actions 时，仓库需允许工作流运行并写入状态文件。

依赖见 [requirements.txt](requirements.txt)：

| 依赖 | 固定版本 | 用途 |
| --- | --- | --- |
| feedparser | 6.0.11 | 读取与解析 RSS |
| beautifulsoup4 | 4.12.3 | 提取 HTML 文本和图片 |
| requests | 2.32.3 | 下载海报、发送 Webhook |

## 配置

仅有两个必填环境变量，没有独立配置文件或命令行参数：

| 环境变量 | 说明 |
| --- | --- |
| `DOUBAN_RSS_URL` | 豆瓣 RSS 完整订阅地址；应先确认能够返回自己的动态条目 |
| `DISCORD_WEBHOOK_URL` | 接收通知的 Discord Webhook 完整地址 |

可从自己的豆瓣页面提供的 RSS 入口获取订阅地址，先检查返回内容是否包含电影动态。代码没有内置用户 ID 到 RSS 地址的转换逻辑，也没有实现登录或访问限制绕过。

程序直接读取环境变量，不会自动加载 `.env`。变量未设置会在启动时产生 `KeyError`。当前每次运行仅使用一个 RSS 地址和一个 Webhook；没有多账号配置列表。

## GitHub Actions 部署

1. Fork 仓库，核对继承的状态文件，按下文“首次部署与状态管理”决定是否重建基线。
2. 在仓库 **Settings → Secrets and variables → Actions** 中添加：
   - `DOUBAN_RSS_URL`
   - `DISCORD_WEBHOOK_URL`
3. 启用 Actions，确保仓库允许工作流具有写权限，且分支规则允许自动提交状态。
4. 打开 **Actions → Douban Movie to Discord → Run workflow**，首次部署建议在默认分支 `main` 上手动执行。
5. 检查日志。全新状态应显示“首次运行：已建立历史基线，不推送旧记录”。之后新增电影标记，待 RSS 更新后再次运行验证。

现有工作流配置：

| 项目 | 配置 |
| --- | --- |
| 定时 | `*/5 * * * *`，UTC 每小时第 0、5、10……55 分钟安排运行 |
| 手动运行 | `workflow_dispatch` |
| 运行环境 | `ubuntu-latest`、Python 3.12 |
| 单次超时 | 3 分钟 |
| 仓库权限 | `contents: write` |
| 并发组 | `douban-movie-sync` |
| 取消正在运行的任务 | `false` |
| 状态提交信息 | `Update Douban sync state` |

这是计划执行频率，不代表严格每五分钟送达；实际通知还取决于 RSS 更新及任务执行情况。工作流没有 push 触发。

**状态保存步骤只在前面的步骤成功时运行**，没有 `if: always()`。如果本轮先发送了部分记录，之后又失败，已写入运行器本地的状态不会由后续步骤提交；下次运行可能重复推送这些记录。推送状态遇到权限或分支冲突时也有同样风险。

## 本地运行

从仓库根目录执行：

```bash
git clone https://github.com/simontian2000/Douban-Movie-Tracker.git
cd Douban-Movie-Tracker
python -m venv .venv
```

激活虚拟环境：

```bash
# Linux / macOS
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```bash
python -m pip install -r requirements.txt
```

将两个环境变量注入当前进程后运行：

```bash
python sync.py
```

例如 Bash 可交互输入，避免把真实值写入命令历史：

```bash
read -r -s -p "Douban RSS URL: " DOUBAN_RSS_URL; echo
read -r -s -p "Discord Webhook URL: " DISCORD_WEBHOOK_URL; echo
export DOUBAN_RSS_URL DISCORD_WEBHOOK_URL
python sync.py
```

程序的状态路径相对于**当前工作目录**，因此请从仓库根目录启动。持续运行需要自行使用 cron 或其他调度器，并确保实例不重叠。本地执行只保存 JSON，不自动提交 Git。不要让本地和 Actions 同时维护同一套状态。

## 首次部署与状态管理

状态文件为 [data/sent_records.json](data/sent_records.json)，包含：

```json
{
  "initialized": false,
  "sent": []
}
```

- `initialized`：是否已建立基线。
- `sent`：已处理条目的 SHA-256 标识数组。初始化阶段也会记录条目标识，即使这些条目从未实际推送。

**仓库已经包含 `initialized: true` 的运行状态。** Fork 或克隆会继承它，不能假定第一次启动必然只建立基线。用于自己的 RSS 时，建议先备份该文件，再将其内容替换为上面的初始结构；使用 Actions 时需将重置后的文件提交到运行分支。下一次运行将以当时可见的电影记录建立新基线。

重置不是历史补发功能：初始化会跳过当前可见的旧记录。若在初始化后人为清空 `sent` 而保留 `initialized: true`，当前 RSS 中的电影记录会被视为新记录并发送。更换 RSS 或 Webhook 前也应明确希望沿用还是重建状态；状态本身没有按账号或目标频道分区。

状态采用直接覆盖写入，没有文件锁或原子替换。已发送列表会持续增长，没有自动清理。JSON 无法读取时程序停止，以防重复推送；合法 JSON 中字段缺失或类型不符也可能导致错误。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `KeyError: DOUBAN_RSS_URL` 或 `DISCORD_WEBHOOK_URL` | 检查环境变量、Actions Secrets 名称及工作流映射；仅创建 .env 文件无效 |
| “RSS 解析失败”或“RSS 没有返回任何条目” | 检查地址和网络，确认返回 RSS 而非登录页、限制访问页或空内容 |
| RSS 有条目，但识别电影记录为 0 | 检查链接是否含 movie.douban.com/subject/，标题是否以看过、想看、在看开头 |
| 第一次没有 Discord 通知 | 全新状态只建立历史基线，需等待之后的新增记录 |
| 没有评分或短评 | 仅提取指定文字格式；个人评分不是影片的豆瓣总体评分 |
| 海报未显示 | 检查下载日志；12 秒下载超时、非 image/ 响应或超过 8 MiB 都会放弃海报 |
| Discord 返回 HTTP 错误 | 核对 Webhook、访问权限和响应；代码无自动重试、429 退避或附件被拒后的纯文本重发 |
| 重复推送 | 检查前次是否部分成功后失败、状态提交是否失败、是否并发运行或重置状态 |
| 修改短评后没有新通知 | 同一链接、状态及日期的记录具有相同标识，修改短评不会改变去重键 |
| “状态文件损坏，停止运行以防重复推送” | 从可信备份恢复 JSON，核对结构；不要直接删除而忽略重建基线的影响 |
| Actions 状态保存失败 | 检查写权限、分支规则和远端提交冲突；先核对已发送情况再恢复 |
| 任务达到 3 分钟超时 | 检查 RSS 请求、网络和待发送数量；RSS 读取没有显式超时设置，工作流总超时仍会终止任务 |

单条解析异常会记录日志后跳过；发送异常则会中止本轮后续发送。海报请求超时为 12 秒，Webhook 请求超时为 20 秒。代码没有发送队列、失败重试队列或定期历史补抓。

## 隐私与安全

- Webhook URL 包含发送权限，RSS 地址可能暴露个人动态或订阅信息；将它们放在 Actions Secrets 或受保护的环境中，不要写入提交或公开截图。
- 日志中的请求异常可能包含 URL，分享日志前应检查并遮盖敏感内容。Webhook 泄露后应重建并更新配置。
- 通知可能包含自己的观影状态、日期、评分和短评，配置前确认 Discord 频道的可见范围。
- 程序会请求 RSS 中的图片地址，应使用可信订阅源；未对图片地址实施域名白名单。
- 海报大小在完整下载后检查，8 MiB 限制不是严格的下载流量或内存上限。图片统一以 poster.jpg 和 image/jpeg 上传，代码没有实际转码。
- 已处理标识存储在公开仓库时也属于公开数据；SHA-256 去重不等于加密或匿名化。
- 工作流暂存整个 `data/` 目录，不要在其中保存凭据或其他不应提交的文件。
- .gitignore 排除了 .env 和虚拟环境，但无法阻止已跟踪敏感文件继续被提交。

## 项目结构

```text
.github/workflows/sync.yml  定时执行、依赖安装及状态提交
sync.py                    RSS 解析、去重和 Discord 推送
requirements.txt           固定版本的 Python 依赖
data/sent_records.json      初始化标记和已处理记录
.gitignore                 本地环境与缓存排除规则
README.md                  使用说明
```

仓库当前没有测试套件、Docker 配置或 LICENSE 文件。本文只描述现有实现；使用与再分发的授权条款请向维护者确认。
