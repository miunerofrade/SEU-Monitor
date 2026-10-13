package monitor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const releaseAPI = "https://api.github.com/repos/miunerofrade/SEU-Monitor/releases/latest"

type ReleaseAsset struct {
	Name string `json:"name"`
	URL  string `json:"browser_download_url"`
}
type Release struct {
	Tag        string         `json:"tag_name"`
	Draft      bool           `json:"draft"`
	Prerelease bool           `json:"prerelease"`
	Assets     []ReleaseAsset `json:"assets"`
}

func versionNumbers(s string) ([3]int, error) {
	var v [3]int
	s = strings.TrimPrefix(strings.Split(s, "-")[0], "v")
	parts := strings.Split(s, ".")
	if len(parts) != 3 {
		return v, errors.New("版本格式无效")
	}
	for i, p := range parts {
		n, e := strconv.Atoi(p)
		if e != nil || n < 0 {
			return v, errors.New("版本格式无效")
		}
		v[i] = n
	}
	return v, nil
}
func newer(current, next string) bool {
	a, e := versionNumbers(current)
	b, f := versionNumbers(next)
	if e != nil || f != nil {
		return false
	}
	for i := range a {
		if b[i] != a[i] {
			return b[i] > a[i]
		}
	}
	return strings.Contains(current, "-") && !strings.Contains(next, "-")
}
func latestRelease(ctx context.Context, client *http.Client) (Release, error) {
	var value Release
	r, e := get(ctx, client, releaseAPI)
	if e != nil {
		return value, errors.New("无法获取最新版本；请稍后再试")
	}
	defer r.Body.Close()
	if json.NewDecoder(io.LimitReader(r.Body, 4<<20)).Decode(&value) != nil || value.Tag == "" || value.Draft || value.Prerelease {
		return value, errors.New("稳定版发布信息无效")
	}
	return value, nil
}
func checkedDownload(ctx context.Context, client *http.Client, release Release, platform string) ([]byte, error) {
	name := "monitor_" + platform
	var binaryURL, checksumsURL string
	for _, a := range release.Assets {
		switch a.Name {
		case name:
			binaryURL = a.URL
		case "checksums.txt":
			checksumsURL = a.URL
		}
	}
	if binaryURL == "" || checksumsURL == "" {
		return nil, errors.New("该版本缺少当前平台程序或校验文件")
	}
	download := func(u string, limit int64) ([]byte, error) {
		r, e := get(ctx, client, u)
		if e != nil {
			return nil, e
		}
		defer r.Body.Close()
		b, e := io.ReadAll(io.LimitReader(r.Body, limit+1))
		if int64(len(b)) > limit {
			return nil, errors.New("更新文件过大")
		}
		return b, e
	}
	checksums, e := download(checksumsURL, 1<<20)
	if e != nil {
		return nil, errors.New("更新校验文件下载失败")
	}
	expected := ""
	for _, line := range strings.Split(string(checksums), "\n") {
		parts := strings.Fields(line)
		if len(parts) == 2 && strings.TrimPrefix(parts[1], "*") == name {
			expected = parts[0]
			break
		}
	}
	if len(expected) != 64 {
		return nil, errors.New("缺少对应平台的 SHA256")
	}
	binary, e := download(binaryURL, 40<<20)
	if e != nil {
		return nil, errors.New("新版程序下载失败")
	}
	if hash(binary) != strings.ToLower(expected) {
		return nil, errors.New("新版程序 SHA256 校验失败")
	}
	return binary, nil
}
func installUpdate(ctx context.Context, path string, data []byte, version string, pause func() (func() error, error)) error {
	old, e := os.ReadFile(path)
	if e != nil {
		return errors.New("无法读取当前程序")
	}
	info, e := os.Stat(path)
	if e != nil {
		return e
	}
	staged, e := os.CreateTemp(filepath.Dir(path), ".monitor-update-*")
	if e != nil {
		return errors.New("程序目录不可写，请检查安装位置或权限")
	}
	name := staged.Name()
	defer os.Remove(name)
	if e = staged.Chmod(info.Mode().Perm()); e == nil {
		_, e = staged.Write(data)
	}
	ce := staged.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return errors.New("新版程序保存失败")
	}
	probeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	output, e := exec.CommandContext(probeCtx, name, "--version").Output()
	cancel()
	if e != nil || strings.TrimPrefix(strings.TrimSpace(string(output)), "v") != strings.TrimPrefix(version, "v") {
		return errors.New("新版程序启动检查失败")
	}
	resume, e := pause()
	if e != nil {
		return e
	}
	restored := false
	defer func() {
		if !restored {
			resume()
		}
	}()
	if e = atomicWrite(path+".previous", old, info.Mode().Perm()); e != nil {
		return errors.New("无法保存旧程序，取消更新")
	}
	if e = os.Rename(name, path); e != nil {
		return errors.New("替换程序失败，旧版本已保留")
	}
	if e = resume(); e != nil {
		rollback := atomicWrite(path, old, info.Mode().Perm())
		resume()
		restored = true
		if rollback != nil {
			return errors.New("服务恢复失败且自动回滚失败；请使用 monitor.previous")
		}
		return errors.New("服务恢复失败，已回滚旧程序")
	}
	restored = true
	return nil
}
func pauseForUpdate(ctx context.Context) (func() error, error) {
	if runtime.GOOS != "linux" {
		return func() error { return nil }, nil
	}
	timer, vpn := active(ctx, timerUnit), active(ctx, vpnUnit)
	resume := func() error {
		c, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		if vpn {
			if e := systemctl(c, "start", vpnUnit); e != nil {
				return e
			}
		}
		if timer {
			return systemctl(c, "start", timerUnit)
		}
		return nil
	}
	if timer {
		if e := systemctl(ctx, "stop", timerUnit); e != nil {
			return nil, e
		}
	}
	waitCtx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	e := waitScan(waitCtx)
	cancel()
	if e != nil {
		resume()
		return nil, errors.New("等待扫描结束失败，取消更新")
	}
	if vpn {
		if e := systemctl(ctx, "stop", vpnUnit); e != nil {
			resume()
			return nil, e
		}
	}
	return resume, nil
}
func Update(ctx context.Context, root, current string, check bool) error {
	client := NewClient("")
	client.Timeout = 2 * time.Minute
	defer client.CloseIdleConnections()
	release, e := latestRelease(ctx, client)
	if e != nil {
		return e
	}
	if !newer(current, release.Tag) {
		fmt.Printf("当前版本 %s 已是最新稳定版\n", current)
		return nil
	}
	fmt.Printf("发现新版本：%s → %s\n", current, release.Tag)
	if check {
		return nil
	}
	binary, e := checkedDownload(ctx, client, release, runtime.GOOS+"_"+runtime.GOARCH)
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
	releaseLock, e := operationLock(exe + ".update.lock")
	if e != nil {
		return e
	}
	defer releaseLock()
	if output, err := exec.CommandContext(ctx, exe, "--version").Output(); err == nil && !newer(strings.TrimSpace(string(output)), release.Tag) {
		fmt.Println("该版本已安装，无需重复更新")
		return nil
	}
	if e = installUpdate(ctx, exe, binary, release.Tag, func() (func() error, error) { return pauseForUpdate(ctx) }); e != nil {
		return e
	}
	fmt.Println("更新完成；旧程序保存在 " + exe + ".previous，配置和归档保持不变")
	return nil
}
