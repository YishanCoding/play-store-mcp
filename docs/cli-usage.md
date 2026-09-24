# gpcli 用法

安装（worktree 可编辑安装）：

```bash
uv tool install --reinstall -e /Users/yishan/本地编程开发/Googleplay-mcp-cli
```

凭据只读环境变量 `GOOGLE_PLAY_STORE_CREDENTIALS`（服务账号 JSON 路径）。可用 `GPCLI_PACKAGE` 作为 `--package` 默认值。

写命令默认 dry-run；加 `--yes` 才执行。高风险命令还要 `--confirm <包名>`，且必须与 `--package` 一致。

## 自描述与验收

```bash
gpcli tools --json
gpcli auth check
gpcli smoke --output /tmp/gpcli-smoke.json
```

## 按 resource 的示例

包名：

```bash
gpcli package-name validate --package com.vast.jujubit
gpcli track validate --track production
```

应用与详情：

```bash
gpcli app get --package com.vast.jujubit
gpcli app-details get --package com.vast.jujubit
gpcli app deploy --package com.vast.jujubit --track internal --file /path/to/app.aab
```

发布：

```bash
gpcli release list --package com.vast.jujubit
gpcli release promote --package com.vast.jujubit --from-track internal --to-track production --version-code 100
```

评论：

```bash
gpcli review list --package com.vast.jujubit --limit 5 --fields reviewId,comments
gpcli review reply REVIEW_ID --package com.vast.jujubit --reply-text "Thanks"
```

商店页：

```bash
gpcli listing get --package com.vast.jujubit --language en-US
gpcli listing-text validate --title "My App"
gpcli image list --package com.vast.jujubit --language en-US --image-type phoneScreenshots
```

国家与自定义页：

```bash
gpcli country-availability get --package com.vast.jujubit --track production
gpcli custom-store-listing list --package com.vast.jujubit --developer-id DEV --app-id APP
gpcli store-listing-experiment list --package com.vast.jujubit --developer-id DEV --app-id APP
gpcli experiment-report get EXPERIMENT --package com.vast.jujubit --developer-id DEV --app-id APP
```

健康度与获客：

```bash
gpcli vitals crash-rate --package com.vast.jujubit --start-date 2026-09-01 --end-date 2026-09-08
gpcli acquisition query --package com.vast.jujubit --developer-id DEV --app-id APP --start-date 2026-09-01 --end-date 2026-09-08
gpcli install-stats get --package com.vast.jujubit --developer-id DEV --app-id APP --start-date 2026-09-01 --end-date 2026-09-08
gpcli search-term query --package com.vast.jujubit --developer-id DEV --app-id APP --start-date 2026-09-01 --end-date 2026-09-08
```

订阅与内购：

```bash
gpcli subscription list --package com.vast.jujubit
gpcli in-app-product list --package com.vast.jujubit
gpcli product-purchase get --package com.vast.jujubit --product-id sku --token TOKEN
gpcli subscription-purchase get --package com.vast.jujubit --token TOKEN
gpcli voided-purchase list --package com.vast.jujubit
gpcli base-plan activate BASE --package com.vast.jujubit --product-id premium
gpcli region-price convert --package com.vast.jujubit --price-amount 9.99 --currency-code USD
```

订单：

```bash
gpcli order get ORDER_ID --package com.vast.jujubit
```

测试人员与产物：

```bash
gpcli tester get --package com.vast.jujubit --track internal
gpcli bundle list --package com.vast.jujubit
gpcli generated-apk list --package com.vast.jujubit --bundle-version-code 100
gpcli expansion-file get --package com.vast.jujubit --version-code 100
gpcli deobfuscation-file upload --package com.vast.jujubit --version-code 100 --file mapping.txt
```

账号权限：

```bash
gpcli user list --developer-id DEV
gpcli grant create USER@example.com --developer-id DEV --package com.vast.jujubit --app-level-permissions '["canReplyToReviews"]'
```

MCP 工具的 kebab-case 名仍可用，例如 `gpcli reply-to-review REVIEW_ID --package com.vast.jujubit --reply-text "Thanks"`。

## 已知问题

- `--profile` 只保留名字，未实现多账号。
- `get_vitals_overview` / `get_vitals_metrics` 在当前 client 上会直接报未实现；请用 `vitals crash-rate` 等 Reporting API 工具。
- `acquisition query`、`install-stats get`、`search-term query`、CSL / 实验列表走 Play Console 浏览器会话（OpenCLI），没有登录态会失败。
- 规格里 3 项浏览器能力只出现在 `gpcli tools --json`（`command: null`），CLI 不实现浏览器自动化。
- 部分 client 方法把 API 错误收成 `success: false` 的 JSON；CLI 在方法抛出 `HttpError` / `PlayStoreClientError` 时才会以退出码 3 失败。
