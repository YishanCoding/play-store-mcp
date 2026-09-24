# 三 CLI 统一约定（ASA / App Store Connect / Google Play）

版本：v1 · 2026-09-23 · 适用：`aads-v1`（ASA，已上线，基准）、`ascli`（ASC）、`gpcli`（GP）

目的：后续 skill 用同一套心智模型调用三个 CLI。基准是已上线的 ASA CLI（`/Users/yishan/projects/仓库-ASA-cli`，`aads-v1 --help`）。ascli/gpcli 与本文冲突时以本文为准；本文没写的，按各自 spec。

## 1. 命令形状

```
<cli> [全局 flag] <resource> <verb> [位置参数] [flag]
```

- `resource` 用单数名词、kebab-case：`app`、`build`、`review`、`version-localization`、`release`、`subscription`。
- `verb` 优先用这组：`list`（简单列表）、`query`（带过滤条件的列表）、`get`、`create`、`update`、`delete`；业务动作用具体动词：`submit`、`cancel`、`release`、`promote`、`halt`、`reply`、`upload`、`refund`。
- 目标对象 id 是位置参数（`ascli review get <id>`），和 `aads-v1 campaign update <id>` 一致。
- 从 MCP 工具名机械生成的命令名只作别名保留（如 `list-reviews`），规范名按本节。`tools` 输出以规范名为准。

## 2. 输入

- `--query` 和 `--body` 接收内联 JSON 对象或 `@path/to/file.json`（与 aads-v1 相同）。
- 常用必填项另给单独 flag，方便手敲：ascli `--app <app-id>`，gpcli `--package <包名>`（可由环境变量 `GPCLI_PACKAGE` 给默认值）。单独 flag 与 `--body` 同时给且冲突时退出码 2。
- 本地文件用 `--file <path>`（截图、AAB 等）。dry-run 时只打印文件名、字节数、MIME、SHA-256（与 aads-v1 asset upload 相同）。

## 3. 输出

- stdout 只放 JSON 数据（默认）。`--format ndjson|table` 可选；`table` 只给人看，skill 不依赖它。
- `--fields a,b.c`：只输出这些字段（点路径）。`--limit N`：最多 N 条。`--all`：自动翻页；与 `--limit` 同时给时 `--limit` 是上限。
- `--verbose`：stderr 打印 method、path、状态码、耗时，不打印任何凭据或 token。

## 4. 写操作安全

- 所有写命令默认 dry-run：stdout 输出 `{"dry_run": true, "method", "path", "body"}`，不发任何网络写请求，退出码 0。
- 加 **`--yes`** 才真正执行（与 aads-v1 相同；不再用 `--apply`）。
- 高风险命令另外必须 `--confirm <app-id|包名>`，而且值要和目标一致，否则退出码 2、不发请求。高风险清单见各自 spec。
- 读请求遇到 429/5xx 按指数退避重试，最多 3 次，并尊重 `Retry-After`；**写请求永不自动重试**。

## 5. 错误与退出码

| 码 | 含义 | stderr |
|---|---|---|
| 0 | 成功（含 dry-run） | — |
| 2 | 参数/用法错误、`--confirm` 不匹配 | `{"error":{"type":"usage","message":...}}` |
| 3 | API 返回错误 | `{"error":{"type":"api","status":<http>,"code":...,"detail":...}}` |
| 4 | 凭据缺失或无效 | `{"error":{"type":"auth","message":"缺 <变量名>"}}` |

错误不能吞：manager 层现有"出错返回空数组"的行为，CLI 层要改成退出码 3。

## 6. 给 skill 用的自描述

- `<cli> tools --json`：输出数组，每项 `{"command", "aliases", "mcp_tool", "kind": "read|write", "risk": "normal|high", "params": <JSON Schema>, "description"}`。skill 靠它查命令，不去读源码。
- 不能由 CLI 完成的能力（只能在浏览器里做、或尚未实现）也列进来：`{"command": null, "capability": "...", "owner": "<opencli 插件或 skill 路径>", "reason": "..."}`。
- `<cli> smoke --output <path>`：只读的线上验收，逐项跑真实只读请求，把结果写成 JSON 报告，任一项失败则退出码非 0。smoke 里不得有写请求。

## 7. 凭据

- ascli：环境变量 `APP_STORE_CONNECT_KEY_ID`、`APP_STORE_CONNECT_ISSUER_ID`、`APP_STORE_CONNECT_PRIVATE_KEY_PATH`。
- gpcli：环境变量 `GOOGLE_PLAY_STORE_CREDENTIALS`（服务账号 JSON 路径）。
- 位置用 `creds show <服务>` 查。凭据值不得出现在代码、测试、日志、文档、dry-run 输出中。
- `--profile` 暂不实现（aads-v1 有多账号需要，ASC/GP 目前各一个账号）；该 flag 名保留，以后加多账号时沿用。

## 8. 运行时

- 用完即退，不常驻；`--help` 冷启动 < 1.5s。
- 安装到 `/Users/yishan/.local/bin/<cli>`。
