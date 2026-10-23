# 数据迁移、通知与回滚

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

首次迁移如需回到旧 SQLite 版本，必须使用迁移前备份的旧服务 unit 与旧 release；旧库原文件仍保留。若 PG 上线后已有新记录，先把增量导回 SQLite，再恢复旧库写入，不能把迁移前 SQLite 当作最新数据直接覆盖线上。导入校验失败时不会启动 PG 版本。

## 通知核查

`notification_jobs.status`：pending、sending、sent、failed、unknown、cancelled。unknown 表示请求或进程中断，可能已送达；先检查群聊，再决定标记 sent 或重新建立任务。失败不影响 RSS 与分析。

新增渠道时先验证发送函数与测试接收端，再注册渠道。注册时对旧消息建立基线，不补发历史；当前有效未来预告可产生后续提醒。渠道停用会阻止任务被领取。只运行一个发送服务实例；重启会将遗留 sending 标记 unknown。

群公告使用客户端“仅发布不通知”，不生成额外群聊天消息。群聊需求收集按所有者手动指令进行，不部署后台监听。
