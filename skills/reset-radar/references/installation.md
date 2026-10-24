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
