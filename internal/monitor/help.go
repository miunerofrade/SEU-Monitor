package monitor

const helpText = `monitor — 东南大学教务处通知监控

用法：
  monitor [参数]
  monitor <子命令> [参数]

命令：
  monitor              启动定时监控，立即扫描一次
  stop                 停止监控和 VPN，取消开机启动
  ps                   查看监控、VPN 和下次扫描时间
  doctor               检查配置、教务处连接和校园通道
  vpn                  配置或重新启动校园 VPN
  log [monitor|vpn]    查看日志，默认显示两类日志
  update               下载、校验并安装最新稳定版

扫描参数：
  --once               前台执行一轮扫描，保存并推送新通知
  --dry-run            检查未处理通知，不归档、不推送、不修改已处理记录
  --interval 秒数      设置扫描间隔，默认 3600 秒，最少 60 秒
  --webhook 地址       设置飞书机器人地址，传空字符串关闭推送
  --data-dir 目录      指定数据目录，默认 ~/.local/share/seu-monitor

VPN 参数：
  --account 账号       设置校园账号，省略密码时交互输入
  --password 密码      设置校园密码
  --port 端口          设置本机 HTTP 代理端口，默认 8888
  --interactive        前台连接 VPN，可交互输入短信验证码

日志参数：
  -f, --follow         实时跟踪日志
  -n, --lines 行数     显示最近指定行数，默认 50 行

更新参数：
  --check              只检查最新版本

通用参数：
  -h, --help           显示帮助
  --version            显示版本
`
