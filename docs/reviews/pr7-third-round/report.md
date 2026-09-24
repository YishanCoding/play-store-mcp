# PR #7 第三轮独立审核

审核日期：2026年9月24日。

仓库：`YishanCoding/play-store-mcp`。
审核 HEAD：`0777ec6bb086f37122e79cbf086dffc7c465e532`。
比较基线：`origin/main`，`98fc7ff0db924eeda84abef080d8ce9428847512`。
前轮 HEAD：`bc18d34e024cef0c75fb0f7f74a01223da994825`。

完整评审已发布到 [PR #7 的 Review](https://github.com/YishanCoding/play-store-mcp/pull/7#pullrequestreview-5302523485)。已读取 PR 正文和前两轮评审。本文件的 F01 至 F07 是本次独立编号。

## 摘要

结论：暂不合并。确认 1 个 P1、4 个 P2、2 个 P3，其中 F06 仅限 CLI 和 MCP 在同一 Python 进程内调用。

受测入口没有复现缺少 `--yes` 仍调用写客户端，也没有复现有效目标的 `--confirm` 不匹配仍执行。但存在参数合并扩大灰度范围、包名冲突检查绕过、失败读取后继续写入，以及分页静默漏数据等问题。

所有发现均存在于审核 HEAD。F01、F02、F06 包含此前引入且在该 HEAD 仍存在的缺陷；F03 将基类原有的 fallback 复制到了第三轮新增 CLI 子类。没有把全部发现都归因于第三轮首次引入。

## 本次提交内容

本提交仅保存审核材料：本报告、`test_pr7_repro.py` 和 `repro-output.txt`。没有修复或修改产品源码、MCP 共用 client、依赖、既有测试或配置。

复现测试存放在 `docs/reviews`，与项目常规 `tests/` 分开。断言刻画审核 HEAD 的错误行为，同时包含正常路径对照。复现用例通过代表成功复现缺陷；修复后需要将相关断言转换为预期契约，不能把这些通过数量当作产品正确性证明。

## 验证边界

原审核环境能够通过 GitHub connector 读取源码，但无法直接克隆或安装完整项目依赖。执行方式是部分离线源码回放：5 个完整 CLI 文件按 Git blob SHA 校验一致后执行，8 个选定工具使用原签名与函数体的源码摘录，模型及基类仅保留被测部分，其余 Google SDK、日志和 FastMCP 边界使用 mock。

记录结果为 22 passed，包含参数化复现及反向对照，见 [原始输出](repro-output.txt)。这不是原仓库完整 pytest 结果，未独立确认 PR 中的 221 passed / 19 skipped，也没有完成 MCP tools/list 字节比较或 FastMCP 协议、transport 级回归。

没有 Google Play 凭据，没有调用 Google Play 业务 API。测试中的 `--yes` 仅作用于 mock 路径，并阻断 socket 连接。F03 未证明 Google 在线接受了错误 body；F04 未取得对应的真实跨页重叠样本；F06 未证明独立进程相互污染。

## 复现方法

在安装了项目依赖的环境中，从仓库根目录运行：

```bash
env -u GPCLI_AUDIT_OFFLINE \
    -u GOOGLE_PLAY_STORE_CREDENTIALS -u GPCLI_PACKAGE \
    PYTHONPATH="$PWD/src" PYTHONDONTWRITEBYTECODE=1 \
    python -m pytest -q -s -p no:cacheprovider \
    docs/reviews/pr7-third-round/test_pr7_repro.py
```

追加 `-k F01` 等可只运行某条发现。此命令使用原生项目依赖，提供给维护者独立复验；原审核实际执行的是上述离线回放环境。

本目录未保存离线 bootstrap 和源码快照，避免把部分替身误当作产品源码。原始离线证据包已在审核会话交付。测试本身在原生运行方式下导入项目模块，不替换为源码摘录。

## F01 · P1：query 输入被默认值覆盖，1% 灰度变成全量发布意图

**file:line：** `src/play_store_mcp/cli/__init__.py:296–321`，关联 `src/play_store_mcp/cli/parser.py:201–204`。

**失败场景：** `release promote --query '{"rollout_percentage":1}'`。解析器先把函数默认值 `100.0` 放入 namespace，合并时先进入 kwargs；query 再调用 `setdefault()`，用户指定的 1 被忽略。body 和独立 flag 对照正常。

安全的直接复现命令：

```bash
gpcli release promote \
  --package com.example.app \
  --from-track internal --to-track production --version-code 1 \
  --confirm com.example.app \
  --query '{"rollout_percentage":1}'
```

实际 dry-run 的 `body.rollout_percentage` 是 `100.0`。

**repro：** `test_F01_query_rollout_default_overrides_one_percent`。执行至 mock 请求边界，query 产生 `status="completed"` 且不含 `userFraction`；body/flag 产生 `status="inProgress", userFraction=0.01`。顺序均为 create edit → source-track GET → target-track UPDATE → commit，exit 0。

**coverage：** `main → parser → _merge_kwargs → tools.promote_release → 原 promote_release 方法 → tracks.update/commit mock`。断言目标包、轨道、完整 release body 和执行顺序。

**claim_supported：true。** 证明接受 1% 输入后生成全量发布意图的请求，不表示发生了实际发布。

修复建议：显式输入合并与冲突检查完成后再补函数默认值，或明确拒绝写命令的 query 输入。

## F02 · P2：flag 缩写绕过包名冲突检查

**file:line：** `src/play_store_mcp/cli/__init__.py:101–109,142–146,308–314`，关联 `src/play_store_mcp/cli/parser.py:20–24`。

**失败场景：** parser 接受 `--pack` 作为 `--package`，但 explicit 集合记录原始 `pack`，没有规范化成 `package_name`。body 可以覆盖已显式指定的包；重复包名检查也只认完整拼写。

```bash
gpcli --pack com.a listing update \
  --language en-US --title T --body '{"package_name":"com.b"}'
gpcli --package com.a listing update \
  --language en-US --title T --pack com.b
```

两条无写命令均 exit 0，目标为 `com.b`。完整 `--package` 拼写的等价冲突返回 exit 2。

**repro：** `test_F02_abbreviated_package_evades_conflict_checks`，body/query/duplicate 三种变体。query 变体保留 `com.a`，但同样遗漏应发生的冲突拒绝；不能把它描述为 query 切到了 `com.b`。

**coverage：** `listing update` 的缩写与 JSON/重复包名组合；检查 dry-run、mock yes 路径的实际 kwargs，以及完整拼写的反向对照。

**claim_supported：true。** 是目标冲突检查绕过；没有证明 yes 或高风险 confirm 门禁失效。

修复建议：所有 parser 层级和别名统一禁用缩写，或按规范化 option/dest 检测冲突。

## F03 · P2：批量更新吞掉 listing GET 的 503 后继续 UPDATE/COMMIT

**file:line：** `src/play_store_mcp/cli/client.py:152–184`。

**失败场景：** `listing batch-update --commit true --yes` 仅改 title，原 listing GET 返回 503。内部捕获所有 HttpError 后视为空 listing，把未提交的描述填为空字符串，然后继续 UPDATE/COMMIT。

**repro：** `test_F03_listing_get_error_swallowed_then_update_commit`。实际顺序 create edit → GET 抛 503 → UPDATE → commit；后续 mock 成功时 exit 0、success=true、commit=true。UPDATE body 为 `{"title":"New","fullDescription":"","shortDescription":""}`。GET 成功对照保留原描述和 video。

**coverage：** `main → tools.batch_update_listings → CliPlayStoreClient.batch_update_listings`。断言 GET 参数、完整 UPDATE body、commit/delete 及执行顺序。另有 `test_control_batch_update_error_is_reported`，证明 UPDATE 自身 403 时正确 exit 3、清理 edit、不 commit。

**claim_supported：true，有明确边界。** 证明读失败被吞和后续错误写入尝试，不证明 Google 接受空描述或线上内容已清空。

修复建议：403/5xx 终止并清理 edit，不能当作 listing 不存在。

## F04 · P2：被过滤的评论先占用 ID，后页同 ID 的有效评论丢失

**file:line：** `src/play_store_mcp/cli/__init__.py:432–439`。

**失败场景：** 第 1 页 r1 没有 userComment；第 2 页 r1 有有效 userComment，另有 r2。代码先把 r1 加入 seen_ids，再过滤，导致后页有效 r1 被当成重复记录。

**repro：** `test_F04_filtered_duplicate_drops_later_valid_review`。应保留 `[r1,r2]`，实际 `[r2]`，exit 0。

**coverage：** `main → _paginate_all → CliPlayStoreClient.list_reviews_page → reviews.list mock → review_from_raw`。断言首次没有 token、第二次 token=T2 的完整请求参数与最终 ID 集合。

**claim_supported：true。** 支撑给定跨页样本的确定性漏数据，未声称已取得 Google 的线上重叠样本。

修复建议：转换成功并决定保留记录后再占用去重 ID。

## F05 · P3：all 与 limit=0 一起使用仍返回一条

**file:line：** `src/play_store_mcp/cli/__init__.py:440–442`。

**失败场景：** `review list --all --limit 0` 且首个页面包含有效评论。先 append 再检查 limit，因而成功返回一条。

**repro：** `test_F05_zero_limit_returns_one_review`。exit 0，返回 1 条，发起 1 次请求。

**coverage：** CLI limit 解析、分页、真实 list_reviews_page 实现及 SDK 请求 mock。

**claim_supported：true。** 违反最多 N 条；若 0 不支持，应明确 usage error。

修复建议：翻页前处理零上限，并校验 limit 范围。

## F06 · P2，仅同进程：CLI dry-run 永久替换 MCP 共用 provider

**file:line：** `src/play_store_mcp/cli/__init__.py:632`，关联 `src/play_store_mcp/tools.py:19–22`。

**失败场景：** 同一 Python 进程先用 server wrapper，再调用 CLI main 的 dry-run。CLI 无条件 configure_client 且退出时不恢复，后续 wrapper 通过共用 tools 取得 CLI client。

**repro：** `test_F06_cli_replaces_shared_mcp_provider_same_process`。CLI 前 server.get_reviews 返回 MCP client 数据；一次 listing update dry-run 后，server.get_reviews 返回 CliPlayStoreClient 数据。dry-run 自身未调用业务客户端。

**coverage：** 原 server wrapper 函数体、tools provider、CLI main，使用 CLI 子类实例和方法 mock。未运行 FastMCP 协议或 transport。

**claim_supported：true，仅限同进程调用链。** 不支持两个独立进程相互污染，也没有证明单纯 import CLI 即触发。

修复建议：作用域化依赖注入。finally 恢复仅处理顺序调用，仍需考虑并发；若只支持独立进程，应明确限制。测试 fixture 的恢复不能代替生产隔离。

## F07 · P3：all 分页绕过只读重试

**file:line：** `src/play_store_mcp/cli/__init__.py:422–424`，关联 `_call_tool()`。

**失败场景：** 第一次评论请求 503，第二次可成功。普通命令经 _call_tool 重试；all 直接调用只包装异常的 list_reviews_page。

**repro：** `test_F07_all_read_does_not_retry_503`。all 请求 1 次后 exit 3；普通对照请求 2 次后 exit 0。

**coverage：** 分页与 list_reviews_page，对照 main 与 _call_tool；检查次数和 HTTP status。

**claim_supported：true。** 只读重试行为不一致，未证明吞错或写请求重试。

修复建议：按单页复用只读重试，避免整段分页重来。

## 已核验的正常行为

main 与 HEAD 的共用 `src/play_store_mcp/client.py` Git blob SHA 均为 `1d8c29cdc4fa1e0825d9a5013f933b795e6ca02a`。文件零 diff 成立；整体运行时隔离需要单独验证。依赖约束已包含 `mcp>=1.26.0,<2`，安装解析与启动兼容性未在原审核环境复跑。

标准全局 flag 在 resource 前、resource/verb 间、verb 后及 reply-to-review 别名通过对照。受测写入口无 yes 时零客户端调用；有效目标 confirm 不匹配、完整拼写 JSON/位置参数冲突、写命令带 all 均调用前拒绝。

分页对照包括 3 页 229 个有效 ID，完整 token/translationLanguage 参数；持续同 token 恰好请求 100 次后 exit 3；第 100 页正常结束则成功且没有第 101 次请求；正数 limit 按有效且去重后的输出计数并停止。CLI app get 的 listing 404 正确 exit 3 并清理 edit。

## 测试质量与 coverage

已阅读原 `tests/test_cli.py` 全 847 行和 `tests/test_cli_client.py` 全 253 行。原测试确实检查若干参数、token 和调用次数，不能概括为只测退出码。缺口集中在组合情形：过滤与去重分别测；UPDATE 失败与 GET 失败后写入缺少交叉；batch 错误转换测试没有 commit=true，也未检查实际 commit 参数；provider 由 fixture 恢复，掩盖同进程状态改变。

实际动态入口：release promote、review reply 及别名、listing update、listing batch-update、user create、order refund、review list、app get。其余 72 个工具没有逐入口动态核验，不能声称全部吞错路径已排清。

## 假设与未验证

未证明 Google 接受 F03 错误 body，未取得 F04 的线上重叠样本。未完成完整 pytest、MCP tools/list 字节比较、协议级运行时回归。已知 get_custom_store_listings 吞错按本轮要求排除，不计入发现。

## 合并意见

暂不合并。F01、F02、F03、F04 应先修复；F06 按同进程支持边界处理并明确隔离契约；F05、F07 为较低优先级。修复后在真实项目依赖环境重跑请求参数与序列断言、原完整 pytest、MCP schema 与运行时回归。
