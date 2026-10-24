# 数据迁移、通知与回滚

## HTTPS 入口

正式地址为 `https://radar.shadowearth.cn`。Caddy 仅监听 TCP 443，使用 Let's Encrypt ACME 的 TLS-ALPN-01 验证，HTTP challenge 与 HTTP 自动跳转均关闭；不需要 DNS API 密钥。安全组只允许业务 TCP 443，原公网 80 规则撤销。网站仅监听 `127.0.0.1:8787`，Caddy 管理接口为 `127.0.0.1:2019`；现有受限 SSH 运维规则保留。

Caddy 采用官方稳定版包和 systemd，配置来自仓库 `deploy/Caddyfile`。首次安装先 mask 服务，避免默认配置占用 80；配置校验完成且安全组已切换后 unmask，再执行正常部署。部署脚本验证内部健康和可信 HTTPS 才报告成功，不会为失败恢复公网 80。

证书、私钥与 ACME 账户在官方服务的 `/var/lib/caddy/.local/share/caddy` 持久目录，由 caddy 用户管理，不随应用 release 切换，不进入 Git。此目录如需备份，应保存在 root 权限受限的仓外目录，恢复时保留属主、权限和目录结构。删除该目录或反复强制签发可能触发 CA 限流。

网站和通知 unit 均设置 `PUBLIC_ORIGIN=https://radar.shadowearth.cn`。通知模板优先采用运行配置，已排队的结构化任务也会使用新域名；历史已发送消息不修改，不补发测试消息。

检查：`systemctl status caddy reset-radar.service reset-radar-notifications.service`；内部健康 `curl -f http://127.0.0.1:8787/health`；外部健康 `curl -f https://radar.shadowearth.cn/health`。使用 `ss -ltnp` 核对 8787/2019 只在回环地址，80 无监听，443 属于 Caddy。通过 `journalctl -u caddy` 检查证书取得和维护日志；证书实际有效期以当前签发结果为准。自动续期需要域名继续解析到此 ECS、443 可达、ACME 出站可达和持久目录可写。

回滚应用时运行 `scripts/rollback.sh`，保留当前 HTTPS-only unit/Caddy/安全组，切换旧代码且使用新 unit 继续监听本机 8787。网站旧源码能通过 `HOST`、`PORT`、`PUBLIC_ORIGIN` 配置运行。不得恢复备份中 `HOST=0.0.0.0 PORT=80` 的旧 unit。若回滚到 HTTPS 改造前的通知模板，脚本会暂停发送服务，避免继续发送旧 IP 链接；通知任务保留，重新部署兼容 HTTPS 的版本后恢复发送。若 HTTPS 故障，先恢复已验证的 Caddy 配置、证书和本机后端，仍不开放 80；无法恢复时保持后端本机监听并报告失败。

## 迁移

1. 使用 SQLite 在线 backup API 创建快照，保存源码 bundle 与旧服务配置。
2. 在同机 rehearsal 数据库导入，逐表按主键排序，对所有字段做 SHA-256 校验；费用列保持双精度。
3. 在独立测试库验证预算事务并发、回滚、序列生成；验证演练库的网页快照与原文数量。
4. 停止网站写入，再取最终 SQLite 备份，导入空的生产 PG；校验成功后启动新版本。失败则回退旧版本/配置，保留 SQLite 原库。
5. 通过公开 API、浏览器与持续的服务重启计数验证。PG 备份目录为 `/opt/reset-radar/backups`，包括原 SQLite、导入校验、PG 自定义格式 dump 和源码 bundle。定期由运维运行 `scripts/backup-postgres.sh`。

PG 只监听回环地址。应用账户无超级用户、创建库或创建角色权限，连接上限 32。所有运行凭据均在 `/etc/reset-radar`，root 0600。

## 备份恢复验证

把 pg_dump 自定义备份恢复到独立空库，核对全部业务表的记录数和内容摘要，不在生产库执行覆盖式恢复。

## 回滚

先停止 `reset-radar-notifications.service` 与网站，保存最新 PG dump。新版本之间可以保持 PG 数据库，切换到上一个兼容 PG 的 release。

首次迁移如需回到旧 SQLite 版本，应参考迁移前备份的配置与旧 release；旧库原文件仍保留。HTTPS 上线后必须保留本机监听和 HTTPS 入口，不得整份恢复旧 unit 的公网 80 配置。若 PG 上线后已有新记录，先把增量导回 SQLite，再恢复旧库写入，不能把迁移前 SQLite 当作最新数据直接覆盖线上。导入校验失败时不会启动 PG 版本。

## 通知核查

`notification_jobs.status`：pending、sending、sent、failed、unknown、cancelled。unknown 表示请求或进程中断，可能已送达；先检查群聊，再决定标记 sent 或重新建立任务。失败不影响 RSS 与分析。

新增渠道时先验证发送函数与测试接收端，再注册渠道。注册时对旧消息建立基线，不补发历史；当前有效未来预告可产生后续提醒。渠道停用会阻止任务被领取。只运行一个发送服务实例；重启会将遗留 sending 标记 unknown。

群公告使用客户端“仅发布不通知”，不生成额外群聊天消息。群聊需求收集按所有者手动指令进行，不部署后台监听。
