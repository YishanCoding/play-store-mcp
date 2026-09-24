# PR #7 第四轮：验证结论与修复方案

基线：`e639382`（第三轮代码 `0777ec6` + Codex 审核材料）。审核报告：`docs/reviews/pr7-third-round/report.md`。

## Claude 对第三轮审核的复现（2026-09-24，本机，原生依赖）

- `docs/reviews/pr7-third-round/test_pr7_repro.py` 在项目 venv 中原生运行：22 passed。这些测试刻画的是缺陷，通过即说明缺陷存在。F01–F07 全部复现。
- F01 用本机安装的 gpcli 做 dry-run 复现，无 `--yes`：
  `gpcli release promote --package com.example.app --from-track internal --to-track production --version-code 1 --confirm com.example.app --query '{"rollout_percentage":1}'`
  输出 `"rollout_percentage": 100.0`。
- F02 同样用 dry-run 复现：
  `gpcli --pack com.a listing update --language en-US --title T --body '{"package_name":"com.b"}'`
  路径是 `.../applications/com.b/...`，exit 0。

结论：审核成立，不合并，修复后再复验。

## 修复方案（按优先级）

| # | 级别 | 问题 | 修法 | 验收 |
|---|---|---|---|---|
| F01 | P1 | `--query` 被函数默认值覆盖（rollout 1% 变 100%） | `_merge_kwargs` 先合并显式输入（flag、位置参数、`--body`、`--query`），做完冲突检查后再填函数默认值。parser 不再把工具默认值写进 namespace（统一 `SUPPRESS`，默认值只在合并阶段补）。 | query、body、flag 三种写法产生相同的 release body（`inProgress` + `userFraction=0.01`） |
| F02 | P2 | 参数缩写（`--pack`）绕过冲突和重复检查 | 所有层级的 `ArgumentParser` 都设 `allow_abbrev=False`，包括顶层、parent、资源、动词、别名。冲突检测改为按规范化后的 dest 比较。 | `--pack` 报 exit 2（unrecognized）；完整拼写的冲突仍然 exit 2 |
| F03 | P2 | batch-update 读 listing 遇 503 被吞，之后仍写入空描述并提交 | `CliPlayStoreClient.batch_update_listings`：只有 404 可以当作 listing 不存在。403、5xx 和其他错误一律中止，删除 edit，不 commit，exit 3。MCP 基类不改。 | GET 503 时请求序列为 create → get → delete edit，没有 update 和 commit，exit 3 |
| F04 | P2 | 被过滤的评论提前占用去重 id，导致后页有效评论丢失 | 转换成功、确认保留后，再把 id 加入 `seen_ids` | 跨页样本返回 `[r1, r2]` |
| F06 | P2 | CLI 的 `configure_client` 永久替换了同进程 MCP 使用的 provider | 改为作用域注入：CLI 调用 tools 时显式传 client，或用 contextvar，在 `try/finally` 中恢复。不让 CLI 修改全局 provider。 | 同进程先跑 CLI dry-run 再调 server wrapper，拿到的仍是 MCP client |
| F05 | P3 | `--all --limit 0` 仍返回 1 条 | 参数解析阶段校验 `--limit`，必须 ≥ 1，否则 exit 2 | exit 2，零请求 |
| F07 | P3 | `--all` 翻页遇到 503 不重试 | 单页请求复用 `_call_tool` 的只读重试策略（有上限，尊重 Retry-After） | 首次 503、第二次成功时，结果为 exit 0 |

## 测试改写

`docs/reviews/pr7-third-round/test_pr7_repro.py` 里的断言描述的是缺陷行为。每修复一项，就把对应断言改成期望的契约，并移到 `tests/` 下（例如 `tests/test_cli_round4.py`）。原复现文件保留在 docs 里作为历史证据，但不能拿它的通过数当作修复正确的证明。

## 不变约束

- `src/play_store_mcp/client.py`、`server.py`、`tools.py` 的 MCP 行为必须与 origin/main 一致。F06 如果必须动 `tools.py`，只允许加可选参数，MCP 调用路径保持原样。
- 只允许 mock 与 dry-run。任何命令都不能带 `--yes` 访问线上。
- 完成标准：
  - `env -u GOOGLE_PLAY_STORE_CREDENTIALS -u GPCLI_PACKAGE .venv/bin/python -m pytest tests/ -q` 全部通过；
  - MCP tools/list 与 origin/main 全量 diff 为 0；
  - PR 正文新增「第四轮」一节，逐项写修复前与修复后的对比。
