# 重置雷达 · Reset Radar

跟踪 Codex / ChatGPT Work 公开重置公告的轻量网站。当前部署在已有阿里云新加坡 ECS，先通过公网 IP 访问。

## 功能

- 最新公开状态、有效预告倒计时和北京时间显示。
- 预告、完成、重置卡分类与搜索，详情和来源原帖。
- RSS 动态订阅与单次日历提醒（提前 15 分钟）。
- 服务端每 5 分钟同步公开 API，按事件 ID 去重并缓存历史。
- 来源不可用或超过 30 分钟未核对时，仅显示历史，暂停有效倒计时。

倒计时结束不代表完成，完成公告也不代表个人账号到账。日历为单次快照，不自动跟随时间修订。HTTP IP 预览尚未提供浏览器后台推送或微信订阅。

## 数据来源

目前使用 [额度星盘的公开只读 API](https://www.starshoreai.com/quota-orbit/api.html)，按接口建议低频缓存，保留出处与原帖。本站没有独立采集或核验每条 X 原帖，不读取任何 OpenAI 账号、个人额度或凭据。历史覆盖不完整。页面视觉与代码独立实现。

## 本地开发

要求 Python 3.12+，无需安装 Python 第三方依赖。Node 仅用于部署前检查 JavaScript 语法。

```bash
python3 -m unittest discover -s tests -v
python3 server.py
# http://127.0.0.1:8787
```

环境变量：`HOST`、`PORT`、`PUBLIC_ORIGIN`、`STATE_DIRECTORY`、`SYNC_INTERVAL`（至少 300 秒）。缓存位于 `STATE_DIRECTORY/events.json`，默认 `.local/events.json`，不进入 Git。

## 服务器开发与发布

- 源码：`/root/Code/reset-radar`
- 版本：`/opt/reset-radar/releases/`
- 当前版本：`/opt/reset-radar/current`
- 缓存：systemd 管理的 `/var/lib/reset-radar/`
- 服务：`reset-radar.service`

```bash
cd /root/Code/reset-radar
git pull --ff-only
bash scripts/deploy.sh
systemctl status reset-radar.service --no-pager
journalctl -u reset-radar.service -n 30 --no-pager
# 有上一版本时回滚
bash scripts/rollback.sh
```

部署脚本测试后保存旧服务配置与版本链接，运行只读站点的隔离 systemd 用户。仅 HTTP/80 对外提供站点；既有 Lark 服务独立运行。AK、SSH 私钥、GitHub 凭据不放入项目。

## 接口与后续入口

- `GET /api/events`：公开事件、新鲜度和首页摘要。
- `GET /rss.xml`：最近 30 条公开消息。
- `GET /reminder.ics`：当前有效预告的日历文件，没有有效预告时返回 409。
- `GET /health`：进程、缓存与消息源状态。

后续可在同一服务前接 HTTPS 域名，接独立官方消息采集，再按实际账号类目接微信订阅消息。域名购买、小程序注册与通知授权均未自动开通。
