# CLI 第一版验收记录

日期：2026-10-24。源码：[PR #25](https://github.com/chenyi/reset-radar/pull/25)。CLI 版本与网站版本分别管理，标签为 `cli-v0.1.0`。

## 已验证

- API v1 已通过 HTTPS 上线：`/api/v1/status`、`/api/v1/events`、`/api/v1/events/{id}`；`/agent` 与 `/openapi.json` 可访问。
- macOS ARM64 和 Linux x86_64 musl 独立二进制已构建，发布归档含 SHA-256 校验。
- Linux 在新加坡实际从官网及 GitHub 下载安装成功，不依赖 Python 或 Rust 环境。
- Linux 实际完成 `0.1.0-rc.1 → 0.1.0 → 0.1.0-rc.1 → 0.1.0`，CLI 与已托管 Skill 同步切换。
- 新加坡隔离测试数据库及真实 Caddy 环境运行 106 项测试全部通过；覆盖 API 时间语义、证据关联、分页、来源过期、429，以及 CLI 错误码、校验失败、下载中断、启动失败、文件冲突、锁冲突和更新失败回退。
- macOS 构建产物的本地回归通过：106 项中 101 项通过，5 项依赖 PostgreSQL/Caddy 的测试在本机跳过；这 5 项已在新加坡通过。此处不等同于 Mac 下载后安装验收。

## 线上两个问题

北京时间 2026-10-24 16:48，通过 Linux 下载版运行 `status --timezone Asia/Shanghai`，同时核对事件详情原文：

1. 当前来源为 `fresh`，分析为 `up_to_date`。最近预告发表于 10 月 23 日 12:47:34，其截止时刻为 10 月 24 日 02:00；查询时截止已过而未确认完成，返回 `forecast_status=elapsed`，不把截止经过推断为完成。[预告原帖](https://x.com/thsottiaux/status/2113492502355562875)
2. 最近一次完成公告发布于 10 月 20 日 14:33:48，实际发生时刻未明确。[完成原帖](https://x.com/thsottiaux/status/2112432076767289678)

这是验收时点的结果，后续应实时调用 API；公开公告不能证明个人额度到账，公开 RSS 也不保证覆盖全部原帖或回复。

## 尚待完成

- Mac 从官网及 GitHub 下载后的真实安装、版本回退和新 Codex 会话自动使用 Skill。
- 受控 Mac 的临时目录执行目前被 SIGKILL；签名校验通过，macOS 策略日志为 allowed。所有者正在通过企业终端安全软件的正常流程放行，复测前不将此项标记通过。
- CLI 0.1.0 稳定发布、官网 `/downloads/install.sh` 与 `/downloads/cli/stable.json`，以及默认 `update --check` 验证。
- 在新加坡完成 PR 合入及主干同步。

## 证据与交接

私有测试和部署记录位于主机 `/opt/reset-radar/backups/cli-acceptance-20261024/`。发布文件在 `/opt/reset-radar/downloads/cli-v0.1.0/`，与 GitHub 候选发布的 SHA-256 一致。

本地最终提交为 `e38650c`，GitHub 和新加坡均已持有全部源码。后续 Git 与代码操作只在新加坡执行；Codex 远程开发连接的配置另行讨论。
