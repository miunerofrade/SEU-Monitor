# SEU-Monitor

东南大学公告监控系统。定时抓取教务处等网站公告，通过飞书机器人推送通知，保存页面快照并自动下载附件。

## 架构

```
SEU-Monitor/
├── monitor.py                  # 主入口（支持 CLI 参数）
├── edulog.py                   # 原入口（保留，兼容已有 workflow）
├── config/
│   └── sites.yaml              # 站点、栏目、路径配置（支持 auth: public/vpn）
├── deploy/
│   ├── env.example             # 环境变量模板
│   ├── run_monitor.sh          # VPS 运行脚本
│   ├── crontab.example         # cron 定时任务示例
│   └── systemd/
│       ├── seu-monitor.service # systemd 服务
│       └── seu-monitor.timer   # systemd 定时器
├── seu_monitor/
│   ├── core/
│   │   ├── settings.py         # 运行时配置（环境变量 / YAML / 默认值）
│   │   ├── healthcheck.py      # VPN/代理健康检查
│   │   ├── http.py             # HTTP 会话（代理、重试、超时）
│   │   ├── ...
│   └── adapters/
├── tests/                      # 49 个测试（不依赖公网）
├── snapshots/                   # 页面快照（.gitignore 排除）
└── store/                      # 已发送 ID 持久化
```

核心管线：**加载配置 → 健康检查 → 抓取列表 → 去重 → 抓详情 → 保存快照 → 推送通知（含摘要） → 标记已见**。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行监控（需要设置 FEISHU_WEBHOOK 环境变量）
export FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
python monitor.py
```

## 命令行参数

```bash
python monitor.py                           # 正常模式
python monitor.py --config path/to/yaml     # 指定配置文件
python monitor.py --site jwc                # 只处理 jwc 站点
python monitor.py --dry-run                 # 试运行（不推送、不 mark_seen）
python monitor.py --check                   # 健康检查模式
```

### --dry-run 模式行为

- ✅ 抓取列表页、详情页
- ✅ 保存快照（raw.html, text.md, meta.json）
- ❌ 不发送飞书通知
- ❌ 不标记已见（不写 sent_ids.txt）
- ❌ 不下载附件

### --check 模式行为

- 加载配置
- 检查环境变量
- 对每个 `auth: vpn` 站点执行健康检查
- 不抓取任何公告

## 快照保存

```
snapshots/<site_id>/<column_id>/<YYYY>/<MM>/<YYYYMMDD_标题前30字>/
├── raw.html         # 完整页面 HTML
├── text.md          # 纯文本 Markdown
├── meta.json        # 元数据（SHA-256、附件记录）
└── attachments/     # 下载的附件
```

## VPS 部署

### 1. 安装依赖

```bash
git clone https://github.com/your/SEU-Monitor.git
cd SEU-Monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp deploy/env.example .env
# 编辑 .env，填入实际值
vim .env
```

`.env` 内容示例：

```bash
MONITOR_CONFIG=/home/ubuntu/SEU-Monitor/config/sites.yaml
STORE_ROOT=/home/ubuntu/SEU-Monitor/store
SNAPSHOT_ROOT=/home/ubuntu/seu-snapshots
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/your_webhook
HTTP_PROXY=http://127.0.0.1:8888
HTTPS_PROXY=http://127.0.0.1:8888
VPN_CHECK_URL=https://cvs.seu.edu.cn
REQUEST_TIMEOUT=20
```

**不要将 `.env` 提交到 Git（已在 .gitignore 中排除）。**

### 3. 检查配置和 VPN

```bash
python monitor.py --check
```

输出示例：

```
SEU-Monitor 健康检查
  配置文件:     config/sites.yaml
  HTTP_PROXY:   http://127.0.0.1:8888
  VPN_CHECK_URL:https://cvs.seu.edu.cn
  站点数: 1
    - jwc (教务处) auth=public 栏目=6
✅ 所有检查通过
```

### 4. 手动运行

```bash
python monitor.py --site jwc
```

或使用运行脚本：

```bash
bash deploy/run_monitor.sh
```

### 5. 定时运行（二选一）

#### 选项 A：cron

```bash
crontab deploy/crontab.example
```

每小时第 15 分钟运行，编辑前请先修改 `crontab.example` 中的路径和 Webhook。

#### 选项 B：systemd timer（推荐）

```bash
# 修改 deploy/systemd/seu-monitor.service 中的路径，然后：
sudo cp deploy/systemd/seu-monitor.service /etc/systemd/system/
sudo cp deploy/systemd/seu-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable seu-monitor.timer
sudo systemctl start seu-monitor.timer
```

查看状态：

```bash
systemctl status seu-monitor.timer
systemctl list-timers seu-monitor
```

推荐使用 **systemd timer**，原因：
- 日志统一由 journald 管理（`journalctl -u seu-monitor.service`）
- 依赖 `network-online.target`，保证网络就绪后才运行
- 支持 `RandomizedDelaySec` 避免整点同时运行

## VPN / 代理

### 配置

`auth: vpn` 和 `auth: mixed` 的站点/栏目在抓取前会进行健康检查。检查通过后才抓取，失败时跳过该站点并发送飞书告警。

> ⚠️ **注意**：当前 JWC 教务处配置为 `auth: mixed`，当 `VPN_REQUIRED=true` 时（默认开启），VPN 掉线会导致 JWC 整体跳过。这是因为 JWC 部分详情页或附件可能依赖校内 VPN，全局检查是为了避免抓取到不完整的页面。
>
> 如果你不需要 VPN 检查 JWC，可以在 `.env` 中设置 `VPN_REQUIRED=false`。这样只有显式标注 `auth: vpn` 的站点才会触发健康检查，JWC 不受影响。

### 判断 VPN 掉线

```bash
curl -k -I -x http://127.0.0.1:8888 --max-time 20 https://cvs.seu.edu.cn
```

如果返回 `tinyproxy 500` 或连接超时，说明 aTrust VPN 已掉线。

## aTrust 自动登录（可选 — 需 aTrust 代理环境）

SEU-Monitor 提供基于 Playwright 的 aTrust 统一认证自动登录模块。

> ⚠️ **前提条件**：此功能需要 VPS 或服务器上已部署 aTrust 代理（通过 aTrust 官方 Docker 镜像或自行构建），并能通过 `http://127.0.0.1:8888` 访问校内资源。还需要 VPS 上有 `chromium` 浏览器镜像（`playwright install chromium`）。
>
> 如果你不需要自动登录功能，可以完全忽略此章节。核心监控（公告抓取、快照、飞书推送）不依赖它，在没有 aTrust 的环境下也能正常运行。

### 安装依赖

```bash
pip install playwright
playwright install chromium  # VPS 上需确保 chromium 镜像可用
```

### 配置

在 `.env` 中设置：

```bash
# 必需：统一认证账号
ATRUST_USERNAME=your_student_id
ATRUST_PASSWORD=your_password
ATRUST_SCREENSHOT_ON_FAIL=true

# ----- 后端选择 -----
# 可选: local / container_cdp
# local: 直接在本机使用 Playwright chromium（调试用）
ATRUST_LOGIN_BACKEND=container_cdp

# ----- container_cdp 模式专用 -----
# aTrust 容器名（docker container ls 查看）
ATRUST_CONTAINER_NAME=atrust
# 容器内 Chromium CDP 端口
ATRUST_CDP_INTERNAL_PORT=9222
# socat 映射到 0.0.0.0 的端口（host 用此端口连接）
ATRUST_CDP_HOST_PORT=9223
# 容器内 Chromium 用户数据目录
ATRUST_CONTAINER_CHROME_USER_DATA_DIR=/root/chrome-atrust-cdp
# 容器内 DISPLAY 环境变量
ATRUST_CONTAINER_DISPLAY=:1
```

**注意：** 账号密码禁止写死到代码、禁止打印到日志、禁止进入飞书消息。

### container_cdp 工作流程

1. healthcheck 失败
2. `docker exec atrust` 启动 Chromium（`--remote-debugging-port=9222`）
3. `docker exec atrust` 启动 socat（`0.0.0.0:9223 → 127.0.0.1:9222`）
4. Playwright 通过 `connect_over_cdp("http://<容器IP>:9223")` 连接
5. 填写统一认证表单
6. 再次 healthcheck 验证

### 手动调试命令

```bash
# 查看容器名
docker container ls

# 手动启动 Chromium（排查启动问题）
docker exec atrust chromium --remote-debugging-port=9222 \
  --user-data-dir=/root/chrome-atrust-cdp --no-sandbox --disable-gpu \
  --disable-dev-shm-usage --ozone-platform=x11 --display=:1 about:blank

# 手动启动 socat
docker exec -d atrust socat TCP-LISTEN:9223,bind=0.0.0.0,fork,reuseaddr \
  TCP:127.0.0.1:9222

# 测试 CDP 端口是否可达
curl http://<atrust_container_ip>:9223/json/version

# 获取容器 IP
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' atrust

# 查看 Chromium 是否在运行
docker exec atrust pgrep -af chrome

# 查看 socat 是否在运行
docker exec atrust pgrep -af socat
```

### 使用

```bash
# 仅检查 VPN 状态
python scripts/atrust_login.py --check-only

# 强制尝试登录
python scripts/atrust_login.py --login

# 默认行为：先检查，失败才登录
python scripts/atrust_login.py
```

### 返回码

| 返回码 | 含义 |
|--------|------|
| 0 | VPN 已可用或自动登录成功 |
| 1 | 自动登录失败 |
| 2 | 自动登录未启用或缺少配置 |
| 3 | 需要人工处理（验证码/扫码/设备绑定等） |

### Watchdog

`scripts/vpn_watchdog.py` 封装了完整流程：healthcheck → 登录 → 飞书通知。

```bash
python scripts/vpn_watchdog.py
```

流程：
1. 调用 VPN healthcheck
2. 如果可用 → 退出 0
3. 如果不可用 → 调用 `atrust_login.py --login`
4. 根据返回码：
   - 0 → 飞书提示"VPN 已自动恢复"
   - 3 → 飞书提示"需要手动处理认证"
   - 1/2 → 飞书提示"自动登录失败，需要手动 VNC"

### Watchdog 定时任务（推荐）

建议使用 systemd timer 每 10 分钟自动检查 VPN 状态：

```bash
sudo cp deploy/systemd/seu-vpn-watchdog.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seu-vpn-watchdog.timer
```

查看状态和日志：

```bash
# 定时器状态
systemctl status seu-vpn-watchdog.timer

# 最近一次执行日志
journalctl -u seu-vpn-watchdog.service -n 50 --no-pager

# 或查看附加日志文件
tail -50 logs/vpn_watchdog.log

# 下次执行时间
systemctl list-timers seu-vpn-watchdog
```

### 截图排障

当 `ATRUST_SCREENSHOT_ON_FAIL=true` 时（默认开启），登录失败会自动截图保存到 `logs/` 目录，文件名格式 `atrust_fail_<tag>_<timestamp>.png`。

**截图可能包含敏感信息（账号、页面内容），请不要公开分享。**

### 当前限制

- ❌ 本系统**不会自动登录**统一认证（需配置 ATRUST_USERNAME/ATRUST_PASSWORD）
- ❌ 不处理验证码、短信验证、扫码认证等二次认证
- ❌ 不绕过 aTrust
- ⚠️ 返回码 3 时需要用户通过 VNC 手动登录

## 配置说明

### 站点级 auth 和代理

```yaml
sites:
  - id: jwc
    name: 教务处
    adapter: wp_news
    auth: public                          # 公开站点，不需要 VPN
    base_url: https://jwc.seu.edu.cn
    columns: [...]

  - id: cvs
    name: 校内资源系统
    adapter: generic_css
    auth: vpn                             # 需要 VPN
    healthcheck: https://cvs.seu.edu.cn   # 健康检查 URL
    proxy: http://127.0.0.1:8888          # 可选，站点级代理覆盖
    columns: [...]
```

| 字段 | 说明 |
|------|------|
| `auth` | `public` 或 `vpn`，决定是否进行健康检查 |
| `healthcheck` | VPN 健康检查 URL（`auth: vpn` 时必填或设置 `VPN_CHECK_URL`） |
| `proxy` | 站点级代理地址，不设则使用环境变量 `HTTP_PROXY`/`HTTPS_PROXY` |

### 路径配置优先级

**环境变量 > YAML 配置值 > 默认值**

| 配置项 | 环境变量 | YAML 键 | 默认值 |
|--------|---------|---------|--------|
| 配置路径 | `MONITOR_CONFIG` | 无 | `config/sites.yaml` |
| 存储根目录 | `STORE_ROOT` | `store_root` | `store` |
| 快照根目录 | `SNAPSHOT_ROOT` | `snapshot_root` | `snapshots` |
| 请求超时 | `REQUEST_TIMEOUT` | 无 | `20` |

## 运行方式

| 命令 | 说明 |
|------|------|
| `python monitor.py` | 新入口，推荐方式 |
| `python monitor.py --check` | 健康检查 |
| `python monitor.py --site jwc` | 只跑 jwc |
| `python monitor.py --dry-run` | 试运行 |
| `python edulog.py` | 原入口，保持兼容 |

## 开发

```bash
# 安装开发依赖
pip install -r requirements.txt pytest

# 运行测试（49 个测试，不依赖公网）
python -m pytest -v
```
