# CLI 第一版验收记录

日期：2026-10-24。CLI 稳定版：[cli-v0.1.0](https://github.com/chenyi/reset-radar/releases/tag/cli-v0.1.0)。源码：[PR #25](https://github.com/chenyi/reset-radar/pull/25)。CLI 版本与网站版本分别管理。

## 已验证

- API v1 已通过 HTTPS 上线：`/api/v1/status`、`/api/v1/events`、`/api/v1/events/{id}`；`/agent` 与 `/openapi.json` 可访问。
- macOS ARM64 和 Linux x86_64 musl 独立二进制已构建，发布归档含 SHA-256 校验；官网与 GitHub 产物字节一致。
- Linux 在新加坡以独立普通账号实际从官网及 GitHub 下载安装成功，CLI 不依赖 Python 或 Rust 运行时。
- Linux 实际完成 `0.1.0-rc.1 → 0.1.0 → 0.1.0-rc.1 → 0.1.0`，CLI 与已托管 Skill 同步切换。
- 独立测试数据库及真实 Caddy 环境运行 106 项测试全部通过，无跳过；覆盖 API 时间语义、证据关联、分页、来源过期、429，以及 CLI 错误码、校验失败、下载中断、启动失败、文件冲突、锁冲突和更新失败回退。UI 分页回归通过。
- 官网 `/downloads/install.sh` 与 `/downloads/cli/stable.json` 已上线；根安装器真实安装通过，默认 `update --check` 能发现稳定版，`update` 能从候选版升级；最新版本再次更新不改文件。
- GitHub-only `update --check` 返回 CLI 0.1.0，未把网站 v0.5.0 当作 CLI 版本。
- 新加坡全新 Codex 会话自动发现并读取已安装 Skill，实际运行 `status --json` 和两次 `events get ... --json`，读取原帖依据后回答两个目标问题。

## 测试区域与执行方式

主机测试区为 `/opt/reset-radar-qa/`，账号 `radar-qa`；测试 HOME 为 `home/`，每轮源码、下载产物、隔离 HOME、会话和证据在 `runs/<UTC时间>/`。本轮为 `runs/20261024T090552Z/`。账号无 sudo，不可读取生产数据库和 AI 配置，不可写线上运行目录。测试数据库由管理员创建、普通测试角色持有，测试结束已删除。

首次 Codex 会话受缺少 Linux 沙箱组件影响，未运行 CLI，不计为通过。随后按 OpenAI 官方流程安装发行版 bubblewrap 并加载 Ubuntu 的 bwrap AppArmor profile；全局用户命名空间限制仍为 1。第二个全新会话在 `workspace-write` 沙箱下成功读取 Skill 并调用 CLI，输出和命令退出码均已核对。

本地 agent 负责分析和通过 SSH 操作主机，主机 Codex 仅用于全新 agent 验收，不接管日常开发。所有者要求后续代码、Git、构建和测试均在新加坡执行，本地仅保留 agent 和开发 Skill。

## 线上两个问题

北京时间 2026-10-24 17:14，全新 Codex 会话查询后回答，并核对原文：

1. 来源为 `fresh`，分析为 `up_to_date`。最近预告发表于 10 月 23 日 12:47:34，其截止时刻为 10 月 24 日 02:00；截止已过而未确认完成，返回 `forecast_status=elapsed`，不把截止经过推断为完成。[预告原帖](https://x.com/thsottiaux/status/2113492502355562875)
2. 最近一次完成公告发布于 10 月 20 日 14:33:48，实际发生时刻未明确。[完成原帖](https://x.com/thsottiaux/status/2112432076767289678)

这是验收时点的结果，后续应实时调用 API；公开公告不能证明个人额度到账，公开 RSS 也不保证覆盖全部原帖或回复。

## macOS 验证范围

Mac 构建本地回归为 106 项中 101 项通过、5 项 PostgreSQL/Caddy 测试跳过，这 5 项已在新加坡通过。Mac 归档签名校验、官网/GitHub 下载字节一致和两个来源的真实安装已验证；企业软件曾终止临时目录执行，正常放行后安装成功。随后所有者要求测试统一迁至新加坡，后续不再执行本机项目测试。

升级回退、稳定更新和全新 Codex 会话的完整端到端验证在 Linux 完成，不把这些结果扩写为全部 Mac 特有行为已验证；未来 Mac 相关变更使用获准的 macOS CI 或测试环境。当前 GitHub CLI 工作流仅构建 Linux。

## 证据与交接

私有测试和部署记录位于主机 `/opt/reset-radar/backups/cli-acceptance-20261024/`，最新测试及会话记录在上述独立测试区。公开发布文件位于 `/opt/reset-radar/downloads/cli-v0.1.0/`，与 GitHub 的 SHA-256 一致。

本地最终提交为 `e38650c`，其后所有集成、文档提交和推送均在新加坡完成。主干合入结果以 PR #25 的实际状态为准，接手流程见 [新加坡主机开发交接](server-development.md)。
