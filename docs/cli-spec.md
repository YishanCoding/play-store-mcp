# gpcli — Google Play Console CLI 规格（给 Grok 实现，Claude 验收）

版本：v2 · 2026-09-23（v2：对齐三 CLI 统一约定，补浏览器能力归属） · 作者：Claude（规划/验收）· 实现：Grok

## 0. 先读统一约定

`/Users/yishan/agent-skills/docs/cli-conventions.md` 是 ASA / ASC / GP 三个 CLI 的共同约定（命令形状、`--query/--body @file`、`--yes`、退出码、`tools --json`、`smoke`）。**本文与它冲突时以它为准。** 参考实现：已上线的 ASA CLI `/Users/yishan/projects/仓库-ASA-cli`（Python + argparse，与本仓库同语言，可直接参考其结构）。

## 1. 为什么做

`play-store` MCP 挂在每个 Claude 会话上常驻（约 60MB/会话），而且**现在根本起不来**：uv tool 环境装到了 `mcp==2.2.0`，`server.py` 用的是 1.x 的 `from mcp.server.fastmcp import FastMCP`，启动即 `ModuleNotFoundError`（Claude 会话里显示 "Connection closed"）。改成 CLI 后不用时零常驻，输出可裁剪省 token，Claude/Codex/Grok/脚本共用。

`src/play_store_mcp/client.py` 的 `PlayStoreClient` 已和 MCP 解耦（`jujubit-review-publish` skill 就是直接 import 它），CLI 直接复用这一层。

## 2. 范围

做：
- **先修依赖**：`pyproject.toml` 把 `mcp>=1.26.0` 改成 `mcp>=1.26.0,<2`，更新 `uv.lock`，让 MCP 本身恢复可启动（不做 2.x 迁移）。
- 新增 CLI 模块 `src/play_store_mcp/cli/`（独立目录，减少和上游 `lusky3/play-store-mcp` 合并冲突），入口 `gpcli = "play_store_mcp.cli:main"` 加进 `[project.scripts]`。
- 覆盖 `server.py` 里全部 `@mcp.tool()`（origin/main 上 80 个）。**不要逐个手写参数**：用 `inspect.signature` + 类型注解 + docstring 从工具函数自动生成子命令（或把工具定义抽成共享注册表，MCP 和 CLI 都从它注册）。MCP 对外工具名/参数/返回必须不变。
- 重新安装：`uv tool install --reinstall -e <worktree 路径>`，产出 `/Users/yishan/.local/bin/gpcli`，同时 `/Users/yishan/.local/bin/play-store-mcp` 恢复能启动。

不做（范围外）：
- 不迁移到 mcp 2.x。
- 不改凭据文件位置/权限（D-CRED-4 另行处理）。
- 不改 skill、不改 `/Users/yishan/.claude.json`、`/Users/yishan/.codex/config.toml`。
- 不向上游提 PR。

## 3. 命令设计

```
gpcli <resource> <verb> [id] [flags]
gpcli tools --json             # 命令目录（统一约定 §6），含全部 MCP 工具映射 + 浏览器能力登记
gpcli auth check               # 等价 validate 类工具
gpcli smoke --output <path>    # 只读线上验收（统一约定 §6）
```

- 规范名按统一约定 §1：单数 resource + 统一动词，例：`review list`、`review reply <review-id>`、`release promote`、`release halt`、`vitals query`、`acquisition query`、`listing update`、`image upload --file`、`order refund <order-id>`。按函数名机械生成的旧名（`reply-to-review`）只作别名。
- 全部工具的 resource/verb 归类由你按上述规则定，交付时 `gpcli tools --json` 就是映射表；拿不准的在交付报告里列出。
- 参数：函数形参 → `--kebab-case`；目标对象 id 用位置参数；`package_name` 统一为 `--package`（默认值取环境变量 `GPCLI_PACKAGE`）；复杂对象用 `--body '<json>'` 或 `--body @file.json`，查询条件用 `--query`。

### 全局 flag（统一约定 §3–§4）

`--format json|table|ndjson`（默认 json）、`--fields a,b.c`、`--limit N`、`--all`（自动翻页）、`--yes`（写操作真正执行，不给就是 dry-run）、`--verbose`（stderr 打请求摘要，不打凭据）。

### 写操作安全规则（硬性）

1. 所有写工具（约 38 个，判据：会改线上状态或产生资金/用户影响）默认 dry-run，打印将发的请求，不发网络写请求。
2. `--yes` 才执行。写请求永不自动重试；读请求 429/5xx 退避重试。
3. 以下高风险命令（按 MCP 工具名列出，对应规范命令同样适用）还必须 `--confirm` 且与目标一致，否则退出码 2：发版/轨道类（deploy、batch-deploy、promote-release、halt-release、update-rollout、deploy-app-multilang）、资金/订阅类（refund-order、cancel-subscription-v2、revoke-subscription-v2、defer-subscription、consume/acknowledge-product-purchase）、删除类（所有 delete-*）、权限类（create/update/delete-user、create/update/delete-grant）、reply-to-review。有 `--package` / `package_name` 时 `--confirm` 必须等于包名；`create_user` / `update_user` / `delete_user` 没有包名，`--confirm` 必须等于 `--developer-id`。

### 不由 CLI 覆盖的能力（写进 `tools --json`，`command: null`，不要去实现浏览器自动化）

| 能力 | 为什么 API 做不了 | 现在谁负责（owner） |
|---|---|---|
| 设置隐私政策 URL | Play Developer API 不提供该字段 | opencli 插件 `opencli-plugin-play-console` 的 `set-privacy-policy`（类别、标签两个适配器已停用） |
| 查看 Android 审核的真实状态 | API 只给轨道/发布状态，看不到 Console 页面上的审核状态 | skill `jujubit-app-review-monitor`（公司电脑上读浏览器页面） |
| 自定义商店页（CSL）上传图片 | 目前哪里都没实现 | 无，登记为 `owner: null, reason: "not implemented"` |

如果实现中发现某个 API 能做到上表里的事，在交付报告里说明，不要擅自改归属。

### 输出与退出码

成功 0；参数错误 2（stderr JSON）；API 错误 3（stderr 带 HTTP 状态和 Google 错误原因，**不能吞**）；凭据缺失 4。

### 凭据

只读环境变量 `GOOGLE_PLAY_STORE_CREDENTIALS`（服务账号 JSON 路径）。位置查询 `creds show google-play`。**任何凭据内容不得出现在代码、测试、日志、文档。**

## 4. 实现要求

- 用独立 worktree，**从 `origin/main` 拉出**：`git -C /Users/yishan/本地编程开发/play-store-mcp fetch origin && git -C /Users/yishan/本地编程开发/play-store-mcp worktree add /Users/yishan/本地编程开发/Googleplay-mcp-cli -b feat/cli origin/main`。主工作区当前在未推送的 `feat/acquisition-breakdown-filters` 分支上，那个分支和它的未提交改动（`uv.lock`、`.connor-output/`、`docs/plans/`）都是用户的，**不要碰、不要把它的提交带进 feat/cli**。
- 工具数以 `origin/main` 的 `server.py` 为准（2026-09-23 实测 80 个；本地未推送分支多 1 个 acquisition 工具，合并后因为 CLI 是自动生成的会自动出现）。验收按 base 分支上的实际数量算，不写死数字。
- 参数解析用标准库 `argparse`，或已在依赖里的库；不要为 CLI 引入重量级新依赖。
- 启动要快：CLI 路径不能 import MCP 服务端框架（把工具函数的"业务部分"和 `@mcp.tool()` 装饰分开，或在 CLI 里只 import 定义模块）。验收会测冷启动时间。
- 测试：沿用 pytest，新增 `tests/test_cli.py`，至少覆盖：映射覆盖全部工具（从 server.py 动态计数，不写死数字）、参数生成、`--fields`、dry-run 零网络调用（mock client）、写请求不重试、`--body @file`、`--confirm` 不匹配拒绝、错误退出码。原有测试全部保持通过。
- 文档：`docs/cli-usage.md`（每个 group 至少 1 条示例 + 已知问题）。

## 5. 验收（Claude 执行，全部通过才算完成）

| # | 检查 | 通过判据 |
|---|---|---|
| G1 | `uv run pytest tests/` | 全绿（原有 + 新增） |
| G2 | `/Users/yishan/.local/bin/play-store-mcp` 以 stdio 启动并响应 `tools/list` | 工具数与 base 分支一致，名字不变 |
| G3 | `gpcli tools --json \| jq '[.[] \| select(.mcp_tool)] \| length'` | = base 分支 `@mcp.tool()` 数；每条有 command、kind、risk、params；另有上表 3 条 `command: null` 能力登记 |
| G4 | `gpcli auth check` | 退出码 0 |
| G5 | `gpcli review list --package <JuJuBit 包名> --limit 5 --fields reviewId,comments` | 只含指定字段，≤5 条 |
| G6 | 一个 vitals 和一个 acquisition 只读命令 | 返回数据，退出码 0 |
| G7 | `review reply` 不带 `--yes` | dry-run，零网络写请求 |
| G8 | `release promote --yes` 缺 `--confirm` | 退出码 2，未发请求 |
| G9 | 错误包名调只读命令 | 退出码 3，stderr 有 HTTP 状态 |
| G10 | `time gpcli --help` | 冷启动 < 1.5s；进程结束后无常驻 |
| G11 | `git diff origin/main --stat`（在 worktree） | 不含用户那 3 处未提交改动，不含凭据 |
| G12 | `gpcli smoke --output /tmp/gpcli-smoke.json` | 退出码 0；报告里每项都是只读请求且通过 |

## 6. 交付物

### 仓库、目录、分支

| 项 | 值 |
|---|---|
| GitHub 仓库 | `YishanCoding/play-store-mcp`（用户自己的 fork；本地 `/Users/yishan/本地编程开发/play-store-mcp`） |
| 上游 | `lusky3/play-store-mcp`（remote `upstream`）——**不要往上游推、不要对上游开 PR** |
| 工作目录 | worktree `/Users/yishan/本地编程开发/Googleplay-mcp-cli`（从 `origin/main` 拉出） |
| 分支 | `feat/cli` |
| 任务 issue | 见派工包（`Closes #<n>`） |

### 提交与 PR

- 把本规格和 `/Users/yishan/agent-skills/docs/cli-conventions.md` 复制到 worktree 的 `docs/`，随第一个提交入库。
- 完成后 `git push -u origin feat/cli`，建 **draft PR**，必须显式指定仓库（fork 上 `gh pr create` 默认会指向上游）：`gh pr create -R YishanCoding/play-store-mcp --base main --head feat/cli --draft`。正文第一行 `Closes #<issue 号>`，后面贴交付报告。
- 不要合并 PR、不要推 main。合并由用户在 Claude 验收通过后决定。
- 提交和 PR 正文里不得出现任何凭据值（仓库是公开的）。
- 交付报告按 `/Users/yishan/agent-skills/docs/codex-review-format.md` 的思路：每条验收项给实际命令+输出摘要；没做到的写"未完成+原因"。
