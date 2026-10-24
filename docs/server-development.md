# 新加坡主机开发交接

项目：[chenyi/reset-radar](https://github.com/chenyi/reset-radar)，远程默认分支 **main**（不是 master）。线上：[https://radar.shadowearth.cn](https://radar.shadowearth.cn)。

## 主机与工作目录

- ECS：`47.237.1.108`，`ap-southeast-1`，实例 `i-t4n3rqhztf9tx2yswgli`。
- 开发 checkout：`/root/Code/reset-radar`。接手先检查 `git status`、分支和 `origin/main`，避免覆盖其他会话正在开发的内容。
- 运行目录：`/opt/reset-radar/current` 指向不可变版本目录；**不要直接编辑 current 或 releases**。在开发 checkout 改代码、建 `codex/` 分支，通过 PR 合入 main，再发布。
- Python：`/opt/reset-radar-tools/python/bin/python`；Node、Git、Caddy 已安装。
- 网站：`reset-radar.service`；通知：`reset-radar-notifications.service`；日志采集：`loongcollectord.service`；采集器健康快照：`reset-radar-observability.timer`。原有其他业务服务不要改动。
- PostgreSQL 在本机，业务库 `reset_radar`，隔离测试库 `reset_radar_tests`。只读费用工具不创建或迁移表。
- 数据库、AI、通知配置分别在 `/etc/reset-radar/database.env`、`ai.env`、`notifications.env`，权限 0600；不要打印或提交到仓库。

公网业务只提供 TCP 443，后端 `127.0.0.1:8787`，Caddy 管理端口与 PG 也仅本机。不要为了开发开放后端、数据库、80 或日志端口。

## 连接

既有 SSH 来源限制保持。SSH 不可达时，可用已登录的个人阿里云 CLI 通过 CloudAssistant 执行命令；不需要把 AK 拷到代码仓库：

```sh
aliyun ecs RunCommand --RegionId ap-southeast-1 \
  --InstanceId.1 i-t4n3rqhztf9tx2yswgli --Type RunShellScript \
  --CommandContent 'cd /root/Code/reset-radar && git status --short --branch' \
  --ContentEncoding PlainText --Timeout 120 --KeepCommand false
# 使用返回的 InvokeId 查询实际退出码和输出；未完成时不要重复发起写操作。
aliyun ecs DescribeInvocationResults --RegionId ap-southeast-1 \
  --InvokeId <返回的InvokeId> --InstanceId i-t4n3rqhztf9tx2yswgli
```

CLI 的 profile/配置文件由接手会话使用已有个人身份选择，不写死凭据。GitHub 公开仓库可以直接拉取；向 GitHub 推送前另行检查主机的写入认证，不假定复制了本机 GitHub 登录态。

## 测试与发布

从仓根运行测试。需明确使用隔离测试库，不能把生产 DATABASE_URL 留给测试：

```sh
cd /root/Code/reset-radar
export PATH=/opt/reset-radar-tools/python/bin:$PATH
set -a
. /etc/reset-radar/database.env
set +a
TEST_POSTGRES_URL="$(python - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit
u = urlsplit(os.environ['DATABASE_URL'])
print(urlunsplit(u._replace(path='/reset_radar_tests')))
PY
)"
test -n "$TEST_POSTGRES_URL"
export TEST_POSTGRES_URL
unset DATABASE_URL
python -m unittest discover -s tests -q
node --check public/app.js
node tests/test_archive_ui.js
# 已审阅、提交且主干同步后发布；脚本也运行必要检查。
bash scripts/deploy.sh
```

`install_observability.sh` 仅在安装或修改采集器配置时运行，不必每次应用发布都重启采集器。SLS 云端配置/查询优先 `aliyun sls`；配置脚本和私人入口见 [日志与费用运维](observability.md)。不安排自动账单核验；SLS 首日实际券抵扣仍在 Issue #21 待手工确认。

## CLI 开发交接

本地 CLI 工作区在最终提交 `e38650c` 后没有未提交代码；该提交已经推送 GitHub 的 `codex/cli-v1`，新加坡主机也已完整拉取。此前交接备份中的 Caddy 冲突已经解决，不再需要恢复旧冲突文件。

- 后续 Git 和代码操作都在新加坡主机执行，不再从 Mac 开发或提交。
- CLI 工作目录：`/root/Code/reset-radar-cli`，分支 `codex/cli-v1`；网站主干工作目录仍为 `/root/Code/reset-radar`。
- [PR #25](https://github.com/chenyi/reset-radar/pull/25) 跟踪 CLI 合入；合入及最终发布状态以 PR 和 [CLI 验收记录](cli-acceptance-2026-10-24.md) 为准。
- 全部源码历史另有私有备份 `/opt/reset-radar/backups/cli-acceptance-20261024/all-source-before-merge.bundle`。
- 分发文件独立存放于 `/opt/reset-radar/downloads/cli-v<版本>/`，由网站 `/downloads/` 提供；发布应用不会覆盖该目录。GitHub Releases 存放相同文件。

现场测试、备份和私人账单在 `/opt/reset-radar/backups/observability-20261024/`。业务源码及发布配置均以远程 main 为准。可观测性验收见 [验收报告](observability-acceptance-2026-10-24.md)。
