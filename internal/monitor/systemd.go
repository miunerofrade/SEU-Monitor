package monitor

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const monitorUnit = "seu-monitor.service"
const vpnUnit = "seu-vpn.service"
const timerUnit = "seu-monitor.timer"

func systemctl(ctx context.Context, args ...string) error {
	if runtime.GOOS != "linux" {
		return errors.New("启动/停止需要 Linux systemd；当前系统可使用 doctor 或 --once")
	}
	cmd := exec.CommandContext(ctx, "systemctl", append([]string{"--user"}, args...)...)
	if cmd.Run() != nil {
		return errors.New("systemd 操作失败；请检查用户会话和 loginctl enable-linger")
	}
	return nil
}
func active(ctx context.Context, unit string) bool {
	return systemctl(ctx, "is-active", "--quiet", unit) == nil
}
func unitQuote(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, `%`, `%%`).Replace(s) + `"`
}
func unitTexts(exe, root string, interval int) map[string]string {
	args := unitQuote(exe) + " --data-dir " + unitQuote(root) + " --worker "
	monitor := "[Unit]\nDescription=SEU-Monitor single scan\n\n[Service]\nType=oneshot\nExecStart=" + args + "monitor\nRestart=no\nTimeoutStartSec=30min\nTimeoutStopSec=45\nUMask=0077\nNoNewPrivileges=true\n"
	vpn := "[Unit]\nDescription=SEU-Monitor VPN\nStartLimitIntervalSec=600\nStartLimitBurst=5\n\n[Service]\nType=simple\nExecStart=" + args + "vpn\nRestart=on-failure\nRestartSec=15\nRestartPreventExitStatus=3\nTimeoutStopSec=45\nUMask=0077\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n"
	timer := fmt.Sprintf("[Unit]\nDescription=SEU-Monitor scheduled scans\n\n[Timer]\nOnActiveSec=1s\nOnUnitInactiveSec=%ds\nAccuracySec=1s\nUnit=%s\n\n[Install]\nWantedBy=timers.target\n", interval, monitorUnit)
	return map[string]string{monitorUnit: monitor, vpnUnit: vpn, timerUnit: timer}
}
func installUnits(ctx context.Context, root string, c Config) error {
	if e := systemctl(ctx, "show-environment"); e != nil {
		return e
	}
	home, e := os.UserHomeDir()
	if e != nil {
		return e
	}
	exe, e := os.Executable()
	if e != nil {
		return e
	}
	exe, e = filepath.EvalSymlinks(exe)
	if e != nil {
		return e
	}
	for name, text := range unitTexts(exe, root, c.Interval) {
		if e = atomicWrite(filepath.Join(home, ".config/systemd/user", name), []byte(text), 0600); e != nil {
			return e
		}
	}
	return systemctl(ctx, "daemon-reload")
}
func startUnit(ctx context.Context, root string, c Config, vpn, restart bool) error {
	if e := installUnits(ctx, root, c); e != nil {
		return e
	}
	unit := timerUnit
	if vpn {
		unit = vpnUnit
	} else {
		systemctl(ctx, "disable", "--now", monitorUnit)
	}
	systemctl(ctx, "reset-failed", unit)
	if e := systemctl(ctx, "enable", unit); e != nil {
		return e
	}
	action := "start"
	if restart {
		action = "restart"
	}
	return systemctl(ctx, action, unit)
}
func stopServices(ctx context.Context) error {
	for _, unit := range []string{timerUnit, monitorUnit, vpnUnit} {
		if e := systemctl(ctx, "disable", "--now", unit); e != nil && unit != monitorUnit {
			return e
		}
	}
	return nil
}
func status(ctx context.Context, root string) {
	for _, unit := range []string{timerUnit, vpnUnit} {
		s := "未运行"
		if active(ctx, unit) {
			s = "运行中"
		}
		fmt.Println(unit + ": " + s)
	}
	cmd := exec.CommandContext(ctx, "systemctl", "--user", "list-timers", timerUnit, "--no-pager")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Run()
	fmt.Println("文件：" + filepath.Join(root, "web"))
}
func logs(ctx context.Context, target string, n int, follow bool) error {
	if n < 1 {
		return errors.New("日志条数必须大于 0")
	}
	args := []string{"--user", "--no-pager", "-n", strconv.Itoa(n)}
	units := []string{monitorUnit, vpnUnit}
	if target == "vpn" {
		units = []string{vpnUnit}
	} else if target == "monitor" {
		units = []string{monitorUnit}
	}
	for _, u := range units {
		args = append(args, "-u", u)
	}
	if follow {
		args = append(args, "-f")
	}
	cmd := exec.CommandContext(ctx, "journalctl", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	e := cmd.Run()
	if ctx.Err() != nil {
		return nil
	}
	if e != nil {
		return errors.New("查看日志需要 Linux journalctl")
	}
	return nil
}
func waitScan(ctx context.Context) error {
	for active(ctx, monitorUnit) {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return nil
}
