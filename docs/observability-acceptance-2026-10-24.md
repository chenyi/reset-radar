# 可观测性验收 · 2026-10-24

关联 [Issue #21](https://github.com/chenyi/reset-radar/issues/21)、[PR #22](https://github.com/chenyi/reset-radar/pull/22)、[PR #23](https://github.com/chenyi/reset-radar/pull/23)、[PR #24](https://github.com/chenyi/reset-radar/pull/24)。

## 交付与证据

- M1：结构化访问、采集、AI、翻译、通知日志；可信边缘生成请求 ID，返回值与后端相同且只有一个。修复畸形 HTTP 请求二次日志异常、root 校验创建访问日志导致 Caddy 重载失败。通知超时仍标记 unknown，不盲目重发；日志补充脱敏异常栈。
- M2：独立新加坡 SLS Project `reset-radar-chenyi-sg`，`access` / `app` Standard、ChargeByDataIngest、30 天。LoongCollector 3.3.10 实际心跳和日志入库均已查询验证。采集配置只绑定指定文件、四个应用 unit，未改原有 CMS。
- M3：[私人看板](https://sls.console.aliyun.com/lognext/project/reset-radar-chenyi-sg/dashboard/radar-overview)及 14 项保存查询，均通过实际 SLS SQL 查询。账号汇总、ECS 分项、百炼券抵扣、应用预算占用及 SLS 状态的只读费用命令已在 ECS 实测。个人账单没有进入仓库。

## 验收结果

| 项目 | 结果与边界 |
|---|---|
| 回归 | ECS 上 78 项 Python 测试全部通过、无跳过，包含真实 Caddy 代理/轮转及隔离 PostgreSQL 的事务和并发预算测试；JavaScript 语法、归档分页回归通过。 |
| 脱敏与伪造头 | 真实 HTTPS 请求带测试 Cookie、Bearer、查询词、完整 Referer 和伪造 XFF/请求 ID；本地与 SLS 日志只留下路径、来源域名和真实连接 IP。返回请求 ID 覆盖访客输入。测试敏感标记未进入日志。 |
| 请求异常 | 线上 404 可按 request_id 关联访问与应用日志；畸形 HTTP 请求用真实后端 socket 测试。隔离真实 Caddy 测试验证 HTTP 500；另用本机临时后端 fixture 产生 HTTP 500，实际 SLS 中有同 ID 的异常和 HTTP 结果。没有给正常页面注入故障分支。 |
| 后台异常 | 临时 fixture 通过指定运维 unit 输出 collection.failed，SLS 能按测试 job_id 查到脱敏错误与文件/行号/函数栈；不调用模型、不向群里发测试消息。 |
| 实际文件轮转 | 线上文件改名并重新打开，新旧文件两侧的测试 request_id 都已入库。Caddy 本身大小轮转另有真实二进制自动测试。旧文件保留作私有证据。 |
| 采集器断网/恢复 | 临时仅限制 loongcollectord.service 的网络，15 秒后解除；期间网站 /health 返回 200。断网期间请求及恢复后请求均入库，随后重启采集器仍继续收集；原生日志明确记录本地 buffer 文件恢复发送成功。临时网络限制和 fixture unit 配置已撤销。 |
| 去重 | 恢复验收复现至少一次投递导致的重复入库，访问概览、路径、来源、错误列表及 p95 按 request_id 聚合去重。原始日志条数与大小估算保留实际入库量，不伪装成唯一访问量。 |
| 健康与积压 | 成功 /health 不进入云端两库；采集器状态含磁盘缓冲、持久检查点年龄和两库原生 flusher 字节/错误计数。磁盘缓冲不含内存队列；写入计数随进程重启归零，不是账单计费量。 |
| 查看入口 | 已用登录中的 SLS 控制台打开私人看板，访问统计与 CLI 查询样本一致；没有开启匿名分享。 |
| 部署边界 | 业务仍只有公网 TCP 443；后端在本机 8787。网站、通知及 LoongCollector 各自独立运行。旧版本、Caddy 配置和源代码备份保留，数据库结构未改。 |

初次 ECS 测试包装命令有括号错误，导致 PostgreSQL 环境变量为空并跳过 4 项；发现后增加非空校验并重新执行。上表采用后续 78 项、无跳过的真实结果，不以最初绿色结果验收。

私人现场证据保存在服务器 `/opt/reset-radar/backups/observability-20261024/`：最终测试/部署日志、`acceptance.json`、访问响应头、轮转日志和费用快照。仓库只保存验收方法与结论，不保存个人账单、密钥或原始访客日志。

## 仍待确认

**SLS 首日实际代金券抵扣尚未出账，不能标记已验证。**已核对通用按量券适用性，但这不等同于实际扣券凭证。费用命令明确返回 `bill_present=false` / `coupon_verified=false`。已安排从 10 月 25 日起在本聊天只读核验；状态未变时不打扰群聊，核验完成后停止跟进。

长期稳定日均成本也不能由一次测试证明；暂以 SLS 5 元/月及整体 300 元/月预算跟进。运维查询和回滚见 [日志与费用运维](observability.md)。
