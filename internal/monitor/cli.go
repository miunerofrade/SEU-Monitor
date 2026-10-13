package monitor

import (
	"context"
	"errors"
	"fmt"
	"golang.org/x/term"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type options struct {
	intervalSet, portSet                                 bool
	command, worker, dir, target                         string
	account, password, webhook                           *string
	interval, port, lines                                int
	interactive, follow, once, dry, check, help, version bool
}

func parseOptions(args []string) (options, error) {
	o := options{lines: 50}
	for i := 0; i < len(args); i++ {
		key, val, has := strings.Cut(args[i], "=")
		next := func() (string, error) {
			if has {
				return val, nil
			}
			i++
			if i >= len(args) {
				return "", errors.New("参数缺少值：" + key)
			}
			return args[i], nil
		}
		switch key {
		case "--help", "-h":
			o.help = true
		case "--version":
			o.version = true
		case "--interactive":
			o.interactive = true
		case "--follow", "-f":
			o.follow = true
		case "--once":
			o.once = true
		case "--dry-run":
			o.once = true
			o.dry = true
		case "--check":
			o.check = true
		case "--data-dir", "--worker", "--account", "--password", "--webhook", "--interval", "--port", "--lines", "-n":
			v, e := next()
			if e != nil {
				return o, e
			}
			switch key {
			case "--data-dir":
				o.dir = v
			case "--worker":
				o.worker = v
			case "--account":
				o.account = &v
			case "--password":
				o.password = &v
			case "--webhook":
				o.webhook = &v
			default:
				n, e := strconv.Atoi(v)
				if e != nil {
					return o, errors.New("数字参数格式无效：" + key)
				}
				switch key {
				case "--interval":
					o.interval = n
					o.intervalSet = true
				case "--port":
					o.port = n
					o.portSet = true
				default:
					o.lines = n
				}
			}
		default:
			if strings.HasPrefix(key, "-") {
				return o, errors.New("未知参数：" + key)
			}
			if o.command == "" {
				o.command = key
			} else if o.command == "log" && o.target == "" {
				o.target = key
			} else {
				return o, errors.New("多余参数")
			}
		}
	}
	switch o.command {
	case "", "vpn", "stop", "ps", "doctor", "log", "update":
	default:
		return o, errors.New("未知命令")
	}
	if o.worker != "" && o.worker != "monitor" && o.worker != "vpn" {
		return o, errors.New("未知 worker")
	}
	if o.target != "" && o.target != "monitor" && o.target != "vpn" {
		return o, errors.New("日志目标应为 monitor 或 vpn")
	}
	return o, nil
}
func Main(ctx context.Context, args []string, version string) (int, error) {
	o, e := parseOptions(args)
	if e != nil {
		return 1, e
	}
	if o.version {
		fmt.Println(version)
		return 0, nil
	}
	if o.help {
		fmt.Print(helpText)
		return 0, nil
	}
	if o.command == "log" {
		return result(logs(ctx, o.target, o.lines, o.follow))
	}
	root, e := DataDirectory(o.dir)
	if e != nil {
		return 1, errors.New("无法创建数据目录")
	}
	c, e := LoadConfig(root)
	if e != nil {
		return 1, e
	}
	changed := o.webhook != nil || o.intervalSet
	if o.webhook != nil {
		c.Webhook = *o.webhook
	}
	if o.intervalSet {
		c.Interval = o.interval
	}
	if e = c.Validate(); e != nil {
		return 1, e
	}
	if o.worker == "vpn" {
		e = RunVPN(ctx, root, c, false)
		if ctx.Err() != nil {
			return 0, nil
		}
		if e != nil {
			log.Print(e)
			if c.Webhook != "" {
				alert(ctx, c)
			}
			return 3, nil
		}
		return 0, nil
	}
	if o.worker == "monitor" || o.once {
		_, e := Scan(ctx, root, c, o.dry)
		return result(e)
	}
	if o.command == "update" {
		return result(Update(ctx, root, version, o.check))
	}
	switch o.command {
	case "stop":
		return result(stopServices(ctx))
	case "ps":
		status(ctx, root)
		return 0, nil
	case "doctor":
		fmt.Println("配置：" + filepath.Join(root, "config.json"))
		fmt.Printf("推送：%t\n", c.Webhook != "")
		proxy := ""
		if c.Account != "" {
			proxy = fmt.Sprintf("http://127.0.0.1:%d", c.Port)
			if !Probe(ctx, proxy) {
				return 1, errors.New("VPN 不可用")
			}
		}
		client := NewClient(proxy)
		defer client.CloseIdleConnections()
		_, e = page(ctx, client, Columns[0].URL)
		if e == nil {
			fmt.Println("教务处：可连接")
		}
		return result(e)
	case "vpn":
		if o.account != nil {
			c.Account = *o.account
			if o.password == nil && term.IsTerminal(int(os.Stdin.Fd())) {
				c.Password, e = readSecret("校园密码：")
				if e != nil {
					return 1, e
				}
			}
		}
		if o.password != nil {
			c.Password = *o.password
		}
		if o.portSet {
			c.Port = o.port
		}
		if c.Account == "" || c.Password == "" {
			return 1, errors.New("请先提供 --account 和密码")
		}
		if e = SaveConfig(root, c); e != nil {
			return 1, e
		}
		if o.interactive {
			systemctl(ctx, "stop", vpnUnit)
			err := RunVPN(ctx, root, c, true)
			if ctx.Err() != nil {
				return 0, nil
			}
			return result(err)
		}
		if e = startUnit(ctx, root, c, true, true); e != nil {
			return 1, e
		}
		fmt.Println("VPN 服务已启动；monitor ps 查看状态")
		return 0, nil
	default:
		if e = ImportLegacy(root); e != nil {
			return 1, e
		}
		if e = SaveConfig(root, c); e != nil {
			return 1, e
		}
		if c.Account != "" {
			if e = startUnit(ctx, root, c, true, false); e != nil {
				return 1, e
			}
		}
		if e = startUnit(ctx, root, c, false, changed); e != nil {
			return 1, e
		}
		fmt.Printf("监控已启动，每 %d 秒扫描；文件：%s\n", c.Interval, filepath.Join(root, "web"))
		return 0, nil
	}
}
func result(e error) (int, error) {
	if e != nil {
		return 1, e
	}
	return 0, nil
}
func ImportLegacy(root string) error {
	marker := filepath.Join(root, "migration.json")
	if _, e := os.Stat(marker); e == nil {
		return nil
	}
	cwd, e := os.Getwd()
	if e != nil {
		return e
	}
	count := 0
	e = filepath.WalkDir(filepath.Join(cwd, "snapshots"), func(p string, entry os.DirEntry, e error) error {
		if os.IsNotExist(e) {
			return nil
		}
		if e != nil {
			return e
		}
		if entry.IsDir() || entry.Name() != "meta.json" {
			return nil
		}
		m, e := readMeta(filepath.Dir(p))
		if e != nil {
			return e
		}
		if m.SiteID != "jwc" {
			return nil
		}
		target := directory(root, m.Notice)
		if _, e = os.Stat(target); e == nil {
			return nil
		}
		if e = copyDirectory(filepath.Dir(p), target); e != nil {
			return e
		}
		m.SnapshotPath = target
		b, _ := jsonBytes(m)
		if e = atomicWrite(filepath.Join(target, "meta.json"), b, 0600); e != nil {
			return e
		}
		count++
		return nil
	})
	if e != nil {
		return e
	}
	for _, c := range Columns {
		b, e := os.ReadFile(filepath.Join(cwd, "store", c.Name, "sent_ids.txt"))
		if os.IsNotExist(e) {
			continue
		}
		if e != nil {
			return e
		}
		for _, id := range strings.Fields(string(b)) {
			if len(id) > 5 {
				if e = markSeen(root, c.Name, id); e != nil {
					return e
				}
			}
		}
	}
	b, _ := jsonBytes(map[string]any{"source": cwd, "snapshots": count})
	return atomicWrite(marker, b, 0600)
}
