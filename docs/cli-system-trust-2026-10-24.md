# CLI 0.1.1：macOS 系统信任修复与验收

日期：2026-10-24。问题：[Issue #27](https://github.com/chenyi/reset-radar/issues/27)。变更：[PR #28](https://github.com/chenyi/reset-radar/pull/28)。

## 问题与修复

所有者 Mac 在企业 HTTPS 检查环境中，系统 curl 可以访问正式 API，而 CLI 0.1.0 返回笼统 network 错误。curl 实际收到系统已信任的阿里郎 CA 签发的证书；旧 CLI 的 Rustls 公共根与系统信任不同。

0.1.1 在 macOS 使用系统 TLS 信任与系统代理，不要求用户导出 CA、设置证书或代理环境变量，不修改系统信任或企业安全软件。Linux 保持 Rustls 独立 musl 构建。证书链和域名校验继续启用，网络错误显示底层原因并移除 URL，防止输出查询参数中的敏感值。

代码、Git、构建调度及发布在新加坡执行；Linux 在隔离测试区重新构建，Mac ARM64 通过 GitHub 的远程 macOS runner 构建，本机只安装发布产物并做真实使用验收。

## 验收证据

- 新加坡隔离 Linux：107 项中 106 项通过，1 项 macOS 专属用例跳过；PostgreSQL/Caddy 环境相关用例通过，临时测试库已清理。
- 两套跨平台 CI 均通过，构建提交为 `c563667`。macOS runner 为 107 项中 102 项通过、5 项 PostgreSQL/Caddy 用例跳过；跳过部分已在新加坡通过。
- macOS 系统信任回归：未受信任的测试 CA 必须失败；导入系统测试信任后，0.1.0 仍复现失败而新版成功；错误域名仍必须失败。测试 CA 只存在远程 CI 临时机器，结束后删除。
- Linux 正式候选产物：官网/GitHub 下载和安装成功，实际从 0.1.0 升级至 0.1.1，CLI 与已托管 Skill 同步。
- 所有者实际 Mac：通过系统 curl 下载正式候选安装器，CLI 和 Skill 从 0.1.0 同步升级至 0.1.1。版本、状态、过去 21 天历史和 Skill 状态均成功，安装文件散列与 receipt 一致。
- 本机没有设置额外的 CA/代理环境变量，没有导入证书、关闭安全软件或更改系统代理设置。本机现有系统代理自动生效。
- 本地全新隔离 Codex 会话读取已安装 Skill，实际执行本地 status、events list 和四次事件详情查询，命令全部成功，没有 SSH 代查。

独立 Codex CLI 首次验收因个人既有模型配置与其账号不匹配而未能启动，这与雷达网络错误不同。后续使用官方 `--ignore-user-config` 的一次性自动化模式验证 Skill，个人配置文件未修改。直接雷达 CLI 查询使用正常本机环境，不依赖该测试选项。

私有构建、CI、升级和本地会话证据在主机 `/opt/reset-radar-qa/runs/system-trust-20261024/evidence/`；发布文件由官网持久下载目录和 GitHub Releases 托管。

## 旧版恢复升级

旧版 0.1.0 在企业证书环境中可能无法访问自己的 update 接口。使用系统自带 curl 的正式安装流程即可恢复，同时更新已安装的 Skill：

```sh
curl -fsSL https://radar.shadowearth.cn/downloads/install.sh -o /tmp/reset-radar-install.sh
sh /tmp/reset-radar-install.sh --agent codex
$HOME/.local/bin/reset-radar --version
$HOME/.local/bin/reset-radar status --json
```

新版正常使用 `reset-radar update --check` 和 `reset-radar update`。查询无需个人 API Key、CA 文件或代理环境变量；它读取系统已存在的正常网络与信任设置，不信任系统本身不信任的证书。
