# 日志与费用运维

入口：[私人 SLS Project](https://sls.console.aliyun.com/lognext/project/reset-radar-chenyi-sg/overview)。新加坡 `ap-southeast-1`，`access` / `app` 两个 Standard 日志库，按写入量计费，保存 30 天。需要本账号的阿里云授权；没有公开日志接口或匿名分享链接。

## 查看与定位

- 看板「重置雷达 · 访问与任务」：访问量、首页成功请求、独立 IP、路径、状态码、5xx、p95、来源域名、浏览器与 IP 粗略地域。访问统计按 request_id 去重，避免采集器至少一次投递的重复入库放大访问量；原始日志和大小估算保留实际入库记录。独立 IP 不是用户数；UA 与来源可由访客填写，仅供辅助判断。
- 浏览器响应 `X-Request-ID` 对应 `access` / `app` 中的 `request_id`；查询 `request_id:具体UUID`。Caddy 覆盖外部传入的 ID，客户端 IP 取连接地址，不信任访客伪造的转发头。
- `app` 按 `job_id`、`request_key`、`provider_request_id` 定位采集、AI、翻译与通知。异常含类型、脱敏消息、文件名/行号/函数栈，不含局部变量和源代码。
- 保存查询定义：[queries.json](../deploy/observability/queries.json)。成功的 `/health` 不入库，失败保留；访问日志本地仍保留成功健康检查。
- 「入库时间」来自 SLS 接收时间标签；事件时间另列。大小面板是日志字段的粗略估算，不是计费量。采集器原始写入量来自本机 flusher 自监控计数，进程累计、重启归零，缺少样本显示 -1；不是计费量。实际写入费用看费用命令及账单。
- 采集器每五分钟记录 `collector.health`。磁盘缓冲文件数、字节数、最老文件年龄、检查点年龄可用于排查积压；不含内存队列，也不等同于端到端延迟。

CLI 查询示例（使用你自己的已登录个人 profile；不要把凭据写进命令）：

```sh
aliyun sls get-logs-v2 --region ap-southeast-1 \
  --project reset-radar-chenyi-sg --logstore app \
  --from <开始Unix秒> --to <结束Unix秒> \
  --query 'level:ERROR | SELECT __time__,event,request_id,job_id,error_type,error_message,stack ORDER BY __time__ DESC LIMIT 50' --line 50
```

服务器本地排查（私有命令，不向群里转发原始输出）：

```sh
journalctl -u reset-radar -u reset-radar-notifications --since '30 minutes ago' --no-pager
tail -n 30 /var/log/reset-radar/access.jsonl
systemctl status loongcollectord reset-radar-observability.timer
journalctl -u reset-radar-observability --since '30 minutes ago' --no-pager
tail -n 30 /usr/local/ilogtail/loongcollector.LOG
```

## 采集与发布

先部署脱敏后的 M1 应用及 Caddy，再绑定采集配置。配置工具默认只展示计划，`--apply` 才写资源，`--bind` 才开始采集。

```sh
python3 scripts/configure_sls.py --apply --bind
# ECS 仓根，以 root 执行；会备份原生采集器配置，保留其他业务配置。
bash scripts/install_observability.sh
```

`ALIYUN_BIN` / `ALIYUN_CONFIG_FILE` / `ALIYUN_PROFILE` 选择已有 CLI 身份；默认使用正常 `aliyun` 配置。CLI 3.3.19 对保存查询的空 `topic` 处理有缺陷，仅该操作使用官方 `aliyun-log-python-sdk`，复用受保护的现有 AK CLI profile，凭据不打印、不复制到应用。安装 SDK 后可重复执行配置工具；其他资源、查询和看板均走 `aliyun sls`。

LoongCollector 3.3.10 独立于网站，走新加坡内网。仅绑定这两个采集配置：Caddy 指定日志文件，以及网站、通知、Caddy、采集器状态这四个 unit 的 journal。没有整机服务日志采集，也没有更改原有 CMS 项目。使用 SLS 原生 Logtail 授权，不向应用部署长期 AK，不替换现有代金券读取角色。

- Caddy：单文件 20 MiB，最多保留 10 个轮转文件、7 天；目录 caddy:caddy 0750，文件 0640。校验配置可能由 root 创建日志，部署脚本在校验后修正所有者。
- 采集器：CPU 0.4、内存 384 MiB，磁盘缓存最多 10×20 MiB，发送原始数据速率目标 4 KiB/s。检查点放在持久目录 `/var/lib/reset-radar-observability`，保留进程重启恢复能力。这些设置不构成账单硬封顶；持续超出缓冲容量可能丢日志。
- 不采集 Cookie、Authorization、完整请求/响应头、正文或 URL 查询参数；Referer 仅域名。UA 截断至 512 字符。公网业务仍只有 TCP 443，后端只监听本机。

## 私人费用查询

在 ECS 执行只读命令。输出包含个人账单，请保存在仓库外并保持私有：

```sh
set -a
. /etc/reset-radar/database.env
set +a
/opt/reset-radar-tools/python/bin/python /opt/reset-radar/current/scripts/costs.py \
  --snapshot /opt/reset-radar/backups/costs-YYYYMMDD.json
```

读取官方账号汇总、ECS 计算/系统盘/流量、百炼实际券抵扣、SLS 和应用 AI 台账。数据库会话强制只读，不创建表。台账有实际金额时不重复累计原预留额；尚未结算请求保留预留额。官方账单与应用估算分开。无 SLS 账单显示 `bill_present=false` / `coupon_verified=false`，不等于免费。

沿用现有 `reset-radar-coupon-reader` 角色与旧策略，只额外附加 `bss:QueryAccountBill` 只读权限（[策略](../deploy/observability/cost-reader-policy.json)）。曾出现 BSS 后端 `NotAuthorized`；诊断中 `callerInfo.errorCode=NotAuthorized`、`httpCode=400`，而 `serviceInfo.reqStatus=success` 表明请求已到达后端。补充必要权限后实调成功；没有修改资源或付款权限。

预算目标：ECS 约 210 元、AI 50 元、SLS 5 元、额外流量等 10 元，总计约 275 元/月；公网流量仍按量计费。SLS 当前公开写入价 0.4 元/GB，30 天以内存储免费，每天 10–100 MB 约 0.12–1.20 元/月写入费，公网读取另计。通用按量代金券的排除清单未列 SLS，只能证明适用性，实际抵扣要等首日账单。

参考：[SLS 价格](https://www.aliyun.com/price/detail/sls)、[按写入量计费](https://help.aliyun.com/zh/sls/billing-per-amount-of-data-written)、[代金券使用规则](https://help.aliyun.com/zh/user-center/how-to-use-coupons)。应用原 AI 月预算及券余额保护保持。

## 回滚

1. 如仅需暂停云端采集，用 CLI 对 `radar-ecs` 移除 `radar-access` / `radar-app` 绑定；本地访问日志继续轮转，网站、采集和通知不受 SLS 故障阻塞。保留日志库与数据，不删除检查点。
2. 应用代码按 `scripts/rollback.sh` 回滚。恢复 Caddy 时，先备份当前文件，再从 `/opt/reset-radar/backups/observability-20261024/Caddyfile.before` 恢复、校验并 reload；校验后检查日志文件所有者。
3. 采集器配置恢复使用安装脚本生成的带时间目录备份；不要覆盖旧检查点或清空缓存。关闭状态定时器用 `systemctl disable --now reset-radar-observability.timer`。

首日 SLS 实际扣券及稳定成本核验在 Issue #21 单独追踪，未出账时保持待核验。
