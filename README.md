# SEU-Monitor

东南大学教务处通知监控命令行。定时抓取六个栏目，保存正文、附件和元数据，可通过飞书机器人推送新通知。不保存原网页 HTML。

监控范围为教务处的六个栏目。校园 VPN 使用原生 zju-connect；监控和 VPN 由 Linux **systemd 用户服务**管理。

## 安装

需要 Python 3.10+、Linux 和 systemd。macOS 可安装、运行测试和 `doctor`，但不支持此处的 systemd 后台启动。

```bash
git clone https://github.com/miunerofrade/SEU-Monitor.git
cd SEU-Monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

激活虚拟环境后可以直接使用 `monitor`。也可执行 `.venv/bin/monitor`，或 `python monitor.py`。

## 基本命令

```bash
monitor           # 启用定时器，启动后扫描一次，此后默认间隔一小时
monitor stop      # 停止监控和 VPN，同时取消开机启动
monitor ps        # 查看定时器、下次运行时间和 VPN 状态
monitor doctor    # 检查配置、教务处连接；配置 VPN 后也检查校园通道
monitor log       # 查看监控和 VPN 最近 50 条日志
```

`monitor` 自动生成 `~/.config/systemd/user/` 下的 `seu-monitor.service`、`seu-monitor.timer` 和 `seu-vpn.service`。通知扫描是一次性服务，由 timer 定时启动，扫描结束后退出；VPN 是常驻服务。重复启动复用同一套服务，从旧版本升级时自动停用原来的常驻监控服务。

systemd timer 负责安排每轮扫描，失败的通知在下一轮重试；等待期间没有监控进程运行。VPN 启动失败、通道失效或需要人工验证时，尝试发送一次飞书告警，随后暂停 VPN 自动重试，监控继续直连抓取。告警发送失败也不会循环重发；未配置飞书时只在日志中记录失败。

排查后执行 `monitor vpn` 手动恢复；需要短信验证时执行 `monitor vpn --interactive`。每次手动恢复后的再次失败会产生一条新的告警。开机时已启用的 VPN 服务仍会尝试连接一次。

VPN 连接成功后每 15 分钟检查一次数据通道，单次请求超时为 10 秒。连续失败 3 次才告警并暂停；中途检查成功会清零失败次数。登录失败、认证要求或核心进程退出仍直接告警并暂停。

VPN 健康检查失败时，监控会改用直连继续抓取。单个栏目或通知访问失败不影响其他内容；成功保存并推送的通知正常记录，失败的部分下一轮重试。VPN 恢复后，后续扫描重新使用校园代理。

如果部署在服务器上，希望退出 SSH 后继续运行并在开机时启动，首次执行：

```bash
sudo loginctl enable-linger "$USER"
```

必须使用同一个普通用户执行 `monitor`，不要用 `sudo monitor`，也不要混用 root 和普通用户的配置。精简版本使用用户服务，旧的 `/etc/systemd/system/seu-monitor*`、VPN watchdog timer 和 cron 请先停用，避免重复扫描或抢占代理端口。

## 定时运行和推送

```bash
monitor --interval 3600           # 每小时扫描，默认值
monitor --interval 1800           # 改成每半小时扫描
monitor --webhook '飞书机器人地址' # 保存推送地址并启动
monitor --webhook ''              # 关闭推送，仅保存通知
```

间隔单位为秒，至少 60 秒，从上一轮扫描结束时开始计算。修改后自动保存并重新启动 timer，随后扫描一次。开机时 timer 自动启用并扫描一次，无需额外配置 cron；同一个扫描服务不会并发运行。

不配置飞书也会归档并去重。配置飞书后，只有正文和附件保存成功、推送成功才标记通知已处理；网络、附件或推送失败都会在下一轮重试。已经保存且 SHA256 校验通过的附件直接复用。

第一次启动会处理列表页当前已有的通知，配置飞书后这些通知也会推送。

## GitHub Actions（按需启用）

本仓库的 `SEU_Monitor` 抓取工作流已停用，日常监控由服务器上的 systemd 服务运行。代码测试工作流仍正常运行。

Fork 后如需使用 GitHub Actions 监控，请在自己的仓库完成以下设置：

1. 在 **Settings → Secrets and variables → Actions** 中添加 `FEISHU_WEBHOOK` Secret，值为飞书机器人地址。
2. 在 **Settings → Actions → General → Workflow permissions** 中选择 **Read and write permissions**，用于保存已推送通知记录。
3. 打开 **Actions**，启用 fork 仓库的 Actions，再进入 **SEU_Monitor → Enable workflow** 启用该工作流。
4. 点击 **Run workflow** 手动执行一次，确认推送正常。

启用后每天北京时间 **08:00** 扫描一次；修改 `.github/workflows/monitor.yml` 的 cron 可调整时间。定时任务的实际启动时间可能延后。GitHub Actions 使用独立的已处理记录，与服务器的数据不共享；同一机器人同时启用两种部署方式会重复推送。

## 校园 VPN

```bash
monitor vpn --account '一卡通号'  # 交互输入密码并保存，启动 VPN 服务
monitor vpn --account '一卡通号' --password '校园密码'
monitor vpn                      # 使用已保存配置重新启动 VPN
monitor ps
monitor doctor
```

省略 `--password` 时交互输入密码，避免密码留在 shell 历史中。以后直接 `monitor` 会使用已保存的账号配置启动 VPN，再启动监控。

默认 HTTP 代理为 `127.0.0.1:8888`，只监听本机，不改系统路由。需要更换端口可用 `monitor vpn --port 8889`。旧 aTrust 容器若仍占用端口，需要先停掉。

首次使用自动下载官方固定版本 zju-connect v1.3.1，校验 GitHub 发布的 SHA256。下载和校园认证直连，避免依赖尚未建立的 VPN 代理；账号密码不传给核心命令行，包含票据的核心原始输出不进入日志。使用纯 HTTP 完成 CAS 密码认证（RSA 加密）和短信验证，不依赖 Playwright 或 Chromium；拦截一次性 CAS 回调，交给核心建立校园通道。

如学校要求短信验证，执行：

```bash
monitor vpn --interactive
```

它会停止后台 VPN，在当前终端用 HTTP 登录、发送短信并提示输入验证码，SSH 中也可以使用，不需要图形桌面。认证完成后代理随这个前台进程运行；退出后执行 `monitor vpn` 恢复后台服务。稳定设备标识保存在 VPN 数据目录中，避免每次随机生成新设备。

学校要求短信验证时需要人工输入验证码。图形验证码暂不支持，遇到时会提示并停止自动重试。

高级场景可用环境变量 `VPN_BINARY` 指定已安装的核心；需写入服务时使用 `systemctl --user edit seu-vpn.service` 添加 `[Service]` 下的 `Environment=VPN_BINARY=/绝对路径/zju-connect`，随后重启服务。默认情况下不需要此配置。

## 配置和文件在哪里

默认根目录为 **`~/.local/share/seu-monitor`**：

```text
seu-monitor/
├── config.json                  # 账号、密码、飞书地址、间隔、代理端口（权限 600）
├── state/<栏目中文名>/sent_ids.txt
├── web/教务处/<栏目中文名>/<通知标题--稳定ID>/
│   ├── text.md                  # 正文、来源链接、发布时间
│   ├── meta.json                # 元数据、哈希、附件结果
│   └── attachments/             # PDF、Word、Excel 等附件原文件
├── vpn/                         # 核心、许可证、设备标识及核心状态（目录权限 700）
└── migration.json               # 旧数据导入记录
```

目录结构参考 SEUdaily：一个通知一个目录，正文和附件一起保存；不会因重新扫描或通知标题变化不断创建新快照。附件名带 URL 哈希，避免同名附件覆盖；元数据记录内容 SHA256，用于复用前校验。下载失败的文件不当作成功附件。

六个栏目：最新动态、教务信息、学籍管理、实践教学、国际交流、文化素质教育。栏目定义集中在 `seu_monitor/sources/jwc.py`。

正文中的图片与 PDF、Word、Excel 等附件一起下载到通知目录的 `attachments/`，`text.md` 在图片原位置保留本地引用。相同链接只下载一次，重试时复用哈希校验通过的文件；图片下载失败会保留原链接并在下一轮重试。图片保留原文件，暂不做 OCR。

从旧仓库升级时，首次在仓库目录执行 `monitor`，会将当前目录的 `snapshots/` 中教务处快照及 `store/` 已处理记录导入新目录。保留旧文件，不覆盖已存在的新通知目录；仅执行一次。旧 `sites.yaml` 和复杂 VPN 环境配置不再使用。不要把配置、认证状态或校园资源提交到公开仓库。

自定义数据位置：

```bash
monitor --data-dir /绝对路径/monitor-data
monitor --data-dir /绝对路径/monitor-data ps
```

后续命令必须使用同一个目录；也可设置 `MONITOR_DATA_DIR`。同一用户只管理一套监控服务，不支持多个数据目录同时启动。

## 日志和服务排查

```bash
monitor log             # 监控和 VPN 最近 50 条日志
monitor log vpn         # 只看 VPN
monitor log monitor     # 只看通知抓取
monitor log -n 100      # 最近 100 条
monitor log vpn -f      # 实时跟踪 VPN 日志，Ctrl+C 退出
```

进一步检查 systemd 状态：

```bash
journalctl --user -u seu-monitor -f
journalctl --user -u seu-vpn -f
systemctl --user status seu-monitor.service seu-vpn.service
systemctl --user list-timers seu-monitor.timer  # 查看下次扫描时间
systemctl --user start seu-monitor.service     # 立即扫描一次
systemctl --user restart seu-vpn.service
```

`monitor` 返回成功意味着 systemd 接受了启动请求；VPN 下载、认证和连接需要时间，实际健康状态请用 `monitor doctor` 和日志检查。

扫描完成后 `seu-monitor.service` 显示 `inactive (dead)` 是正常状态；`seu-monitor.timer` 应显示 `active (waiting)`，到下次时间会重新启动扫描。

遇到 `Failed to connect to bus`，先确认这是有 systemd 用户会话的 Linux 主机，并执行 `loginctl enable-linger`。在 macOS、没有 systemd 的容器或 CI 中，不使用后台启动命令。

## 代码结构

```text
seu_monitor/
├── cli.py                       # 命令入口
├── config.py                    # 精简配置读写
├── systemd.py                   # 服务生成及生命周期管理
├── migration.py                 # 旧数据导入
├── sources/jwc.py               # 教务处栏目
├── adapters/wp_news.py          # 网页解析
└── core/
    ├── runner.py                # 单次扫描管线
    ├── snapshot.py              # 通知归档
    ├── attachments.py           # 附件下载
    ├── vpn.py                   # 原生核心与代理生命周期
    ├── cas.py                   # 纯 HTTP CAS 密码/短信认证
    ├── state.py                 # 去重状态
    ├── notify.py                # 飞书推送
    └── http.py / healthcheck.py / models.py / settings.py
```

`edulog.py` 保留为旧定时任务的一次扫描入口，共用抓取实现，不启动 systemd 服务。正常本地部署使用 `monitor`。

## 开发

```bash
pip install -e '.[dev]'
python -m pytest -q
```

测试覆盖配置、服务命令、迁移、附件保存及复用、CAS 票据拦截和短信认证，通过模拟网络、VPN 核心和 systemd 运行。

zju-connect 为独立的可选 AGPL-3.0 程序，使用未修改的官方发布。自动安装会在核心旁保存 LICENSE 和 SOURCE.txt；对应[固定版本源码](https://github.com/Mythologyli/zju-connect/tree/5d7f5b11fcf231f72a0ec0d888bf0f2eadcce1da)。本项目自身代码许可证保持不变。

## 免责声明

本项目为非官方工具，仅供学习交流及在授权范围内查询、归档校园通知。使用者应遵守学校的信息管理规定，妥善保管账号、认证信息、下载文件和推送地址，不得将受限校内信息公开、转发给未获授权的人员或用于其他未经授权的用途。

因使用者滥用本仓库、配置不当或擅自传播资料而造成的校内信息泄露及其他后果，由使用者自行承担；在法律允许的范围内，项目作者与贡献者不承担相应责任。
