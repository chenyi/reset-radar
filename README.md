# 重置雷达 · Reset Radar

采集 Tibo（@thsottiaux）的公开原帖及回复，按原文展示真实重置预告、进展、完成、重置卡及取消更正消息，保留历史归档。

## 当前数据路径

- 上游：`https://nitter.kareem.one/thsottiaux/with_replies/rss`，按 RSS 的 40 分钟 TTL 轮询。
- 历史：从 2026-04-23 开始按七天窗口分页回填；逐页保存 cursor，失败后保留断点。
- 镜像穷尽某个窗口只表示公开来源返回结束，不能保证覆盖 X 的全部原帖、回复或删除内容。
- `posts` 保存作者原文、独立的引用文本、时间、X 链接、RSS 原始 item 与内容散列。回复 ID 无可靠证据时留空。
- 关键词仅供内部候选；不会直接进入页面或 RSS。AI 排除请求、许愿、玩笑、一般讨论和普通额度调整。Qwen 分析保存逐字证据与版本，时间必须有原文依据；PST 与 PT 分别换算。
- 旧版第三方整理结果保留于 `events.json`；不混作 Tibo 原帖证据，不再作为当前页面数据源。

## 存储与本地开发

未配置 PostgreSQL 的本地开发环境中，状态目录中的 `radar.db` 使用 WAL 与外键。表包括 `posts`、`post_analyses`、`reset_events`、`event_posts`、`collection_jobs`、`sync_state`、`ai_requests`。

原帖 ID 使用 TEXT；时间使用 UTC，页面显示北京时间。分析保存输入快照与版本。历史消息保留原始发布时间；RSS 是否提醒由阅读器决定。

运行时数据库和凭证不进入 GitHub。备份运行中的 SQLite 应使用 `sqlite3.Connection.backup()`，不要只复制主文件。

## 访问接口

- `/`：公开页面，包含信号与可搜索的原帖归档。
- `/api/events`：信号、逐条原帖证据、采集进度及来源状态。
- `/api/posts?q=reset&offset=0&limit=20`：分页原帖，支持原文、日期和 ID 搜索。
- `/rss.xml`：最近 30 条已分类的原文消息，不包含未分析候选和一般讨论。
- `/reminder.ics`：仅在存在有效未来排期时生成单次日历，否则返回 409。
- `/health`：进程、原帖数量与来源状态。

订阅是否提醒由 RSS 阅读器或日历设置决定。未接入微信和浏览器后台推送。公开公告无法证明某个用户的个人额度到账。

本地 AI agent 接入见 [HTTP API、Rust CLI 与 Skill 设计](docs/agent-access-v1.md)。客户端与 Skill 同仓，发布独立二进制；安装说明位于 `/agent`，接口描述位于 `/openapi.json`。二进制和配套 Skill 通过域名与 GitHub Releases 分发，正式版本与验收结果见 CLI Release。

```sh
reset-radar status --json
reset-radar events list --since 7d --type reset --json
reset-radar events get <event-id> --json
reset-radar update --check --json
reset-radar update
reset-radar skill status --json
```

新接口为 `/api/v1/status`、`/api/v1/events`、`/api/v1/events/{id}`。只读查询包含来源新鲜度和原帖依据，不调用模型。完成公告时间与实际发生时间分开；来源过期不能当作没有消息，截止时间经过不能当作完成。CLI 支持 macOS ARM64 和 Linux x86_64，无需安装语言运行时。

## 验证与运行

```sh
python3 -m unittest discover -s tests -v
node --check public/app.js
python3 server.py
```

本地默认监听 `127.0.0.1:8787`。`STATE_DIRECTORY` 控制数据库位置。历史初始范围可通过 `HISTORY_START`、`HISTORY_END` 指定，结束日期不包含当天。每个来源请求间隔至少 3 秒。

## 阿里云部署

源代码位于 `/root/Code/reset-radar`。提交后运行 `scripts/deploy.sh`，会先执行测试，再生成不可变 release 并切换 `/opt/reset-radar/current`；上个 release 保留在 `previous`。网站服务为 `reset-radar.service`，生产数据库为仅本机访问的 PostgreSQL。Caddy 对外仅开放 TCP 443；网站后端仅监听 `127.0.0.1:8787`，关闭公网 80，不提供 HTTP 跳转。证书使用免费 Let’s Encrypt、TLS-ALPN-01 验证并自动续期。配置和回滚见 [运维说明](docs/operations.md)。

首次回填在服务后台执行；来源失败时保持原帖归档可读并明确显示缺口。重启会继续未完成窗口。

## AI 与费用保护

个人账号新加坡 `qwen3.7-flash-2026-07-15`，只在后台处理新增或变化的原帖，不因页面访问重复调用。关闭思考模式与联网搜索。每批最多二十条，最多生成 4096 tokens；限制返回 ID 为本批目标；引用直接取已存原文，时间由原文规则解析，验证分类及事件关联后再入库。

- 个人代金券抵扣实测：`sfm_inference_public_cn`，测试费用 0.001052539 元由代金券全额抵扣。
- 专用 Key 限定服务器 IP 与单个模型；百炼侧月预算 50 元、达预算即停。应用也在发请求前预留费用并执行同一月预算。
- 每次处理前核对券状态；余额查询最多缓存 60 秒。可用通用券低于 100 元、账单出现非券金额或查询失败时停止 AI，RSS 采集继续。
- 实例角色 `reset-radar-coupon-reader` 只有 `bss:DescribeCoupons`、`bss:DescribeBillList` 两项只读权限；不需要把个人 AK 放进应用。
- 服务端 `/etc/reset-radar/ai.env` 存放专用模型 Key（root 0600）；官方 Tea OpenAPI SDK 使用实例角色自动获取临时凭证。凭证不进入源码或公开接口。
- 阿里云账单和预算停止均存在延迟。100 元余额缓冲用于覆盖网站和其他云资源的延迟出账；不能把 50 元预算当作供应商的“仅用券支付”开关。

原文分类保存在 `post_analyses.relevant/category`，包括 forecast、progress、completed、credit、correction。页面默认只展示收录结果，不展示 AI 改写标题或摘要；原始归档折叠保留。原文引用和社区注释分别保存，不充当作者本人证据。SQLite schema 迁移版本为 3。

AI 运行依赖安装在 `/opt/reset-radar-tools/python` 虚拟环境，使用 `requirements.txt`。模型响应在 `ai_requests.response_json` 留存，已收到的响应验证重放不会重复调用模型。

引用帖通过 RSS 的引用链接保存 `quote_id`。只有完成用语的引用链能指向 Tibo 的重置公告时，才作为上下文完成回应；孤立的 Done 不当作完成。中断请求仍保留费用预留，每批最多自动重试一次；接口错误详情仅保存于服务器排障状态。

## PostgreSQL 与通知发送

线上使用同机 PostgreSQL 16，仅监听回环地址。应用从 root 0600 的 `/etc/reset-radar/database.env` 读取 `DATABASE_URL`。未设置时仍可使用 SQLite 做本地开发。原帖 ID 和 UTC 时间保持原有语义；费用列使用 DOUBLE PRECISION，迁移逐表逐值验证，避免 PostgreSQL REAL 降低精度。

`postgres.py` 保持已有查询与行访问契约；AI 月预算预留使用事务级 advisory lock，防止并发超额预留。数据库口令不进入 Git 或命令输出。

通知由 `notifications.py` 生成持久任务，`notification_worker.py` 独立发送，不读取 RSS、不调用模型。`notification_channels` 保存渠道开关，`notification_events` 保存事件版本，`notification_jobs` 保存计划时间、状态、尝试次数和发送结果。Webhook 仅保存在 `/etc/reset-radar/notifications.env`，钉钉机器人限制服务器 IP，不开启 Outgoing。

首个渠道为钉钉；未来渠道只需实现接收标准消息的发送函数并注册 kind。新预告、明确时间前 15 分钟、确认完成及取消更正按事件去重。历史采用基线，不补发；普通进展不发送。截止时间明确标记为截止；来源过期、时间已过或预告被替代时取消提醒。超时或发送中断标记 unknown，须先核查群内结果再人工处理，不自动重发。

```sh
node tests/test_archive_ui.js
# 可选：在空的专用测试数据库验证 PG 事务、预算并发与精度
TEST_POSTGRES_URL=postgresql://... python3 -m unittest discover -s tests -v
# 导入前由运维设置 DATABASE_URL；目标必须为空
python3 scripts/migrate_postgres.py /path/to/sqlite-backup.db
# 服务器 root 下备份
scripts/backup-postgres.sh
```

运行凭据、备份和验收截图存放在仓外。迁移与回滚见 [运维说明](docs/operations.md)，需求审批和群聊整理见 [项目流程](docs/project-workflow.md)。

## 项目跟踪

[在线站点](https://radar.shadowearth.cn/) · [问题与需求](https://github.com/chenyi/reset-radar/issues) · [项目看板](https://github.com/users/chenyi/projects/1) · [版本发布](https://github.com/chenyi/reset-radar/releases)

[2026-10-23 独立页面测试与迁移验收记录](docs/qa/2026-10-23.md)。[2026-10-24 仅 443 HTTPS 交付报告](docs/qa/https-2026-10-24.md)。新需求先待审批，由所有者明确批准后开发。

## 中文阅读与样式

默认首页、收录消息和详情显示简体中文，逐条保留“查看作者原文”。公开重置消息及其关联证据的译文保存在 `post_translations`，按 post_id、原文散列、locale 和提示词版本区分；原文不被覆盖。译文不参与分类、重置状态或时间计算。原文归档仍保留完整原文。

翻译使用同一个个人新加坡百炼模型与共享 50 元月预算，先核对代金券，再锁定预算预留，保留 usage、请求 ID 和响应；关闭 AI 时不调用翻译。页面访问只读数据库。后台只翻译公开收录消息及证据，新帖新增翻译；历史手动处理入口是 `scripts/backfill_chinese.py`。

[样式备选](https://radar.shadowearth.cn/style-options) 提供三种通知排版、三种时钟和三个机器人头像。默认采用清晰中文通知、轨道数字钟（有效预告显示预计时刻，无预告显示当前北京时间）和薄荷绿小钟机器人。通知 worker 可用 `NOTIFICATION_STYLE=clear|brief|bilingual` 切换排版；不配置时为 clear。

生成素材与提示词见 [视觉素材说明](docs/visual-assets.md)。

## 日志与费用

私人 SLS 看板、CLI 查询、只读费用命令及回滚方式见 [日志与费用运维](docs/observability.md)。日志不提供公共接口；个人账单快照保存在仓库外。

远程主机继续开发前请阅读 [新加坡主机开发交接](docs/server-development.md)。
