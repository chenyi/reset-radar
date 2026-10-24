# 安装与更新

安装说明：https://radar.shadowearth.cn/agent

用户明确要求安装后，可下载该页面给出的固定版本安装器，安装 CLI 与 Codex Skill。CLI 无需 Python、Rust 或 Node.js，支持 macOS ARM64 和 Linux x86_64。

安装位置：`$HOME/.local/bin/reset-radar`；Codex Skill：`$HOME/.agents/skills/reset-radar`。它们由版本目录的符号链接管理，不要手动覆盖这些文件。若不在 PATH，使用 CLI 的绝对路径。

```sh
reset-radar --version
reset-radar status --json
reset-radar skill status --json
reset-radar update --check --json
reset-radar update
reset-radar skill install --agent codex
reset-radar skill update
reset-radar skill remove
```

`update` 更新 CLI 和已由它安装的 Skill；未安装 Skill 时不会自动新增。`update --version 0.1.0` 可显式选择版本或回退。`skill update` 同步当前 CLI 对应版本；升级整套工具用 `update`。

`--download-source site|github|auto` 选择下载来源，默认 auto。所有来源校验相同发布文件散列。遇到用户修改或同名路径冲突时保留内容并停止，不删除其他 Skill 或修改 agent 配置。

Skill 安装成功后用新 Codex 会话验证发现和自然触发。安装文件存在、CLI 可运行和真实查询成功是不同的检查项。

## macOS 企业网络

CLI 0.1.1 起使用 macOS 系统 TLS 信任和系统代理，自动识别系统已信任的企业 CA，无需额外设置证书路径、代理变量或关闭校验。网络错误保留底层原因；不要因为证书或连接错误推断网站拒绝访问。

旧版 0.1.0 在企业证书环境中可能连更新接口也失败。用户已经要求升级时，用系统 curl 下载正式安装器后重新安装，即可同时更新 CLI 和已安装的 Skill：

```sh
curl -fsSL https://radar.shadowearth.cn/downloads/install.sh -o /tmp/reset-radar-install.sh
sh /tmp/reset-radar-install.sh --agent codex
$HOME/.local/bin/reset-radar --version
$HOME/.local/bin/reset-radar status --json
```

不要使用跳过 TLS 校验、关闭企业安全软件、手工覆盖托管二进制或编辑托管 Skill 的方式解决网络问题。
