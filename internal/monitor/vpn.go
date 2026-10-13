package monitor

import (
	"archive/zip"
	"bufio"
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"syscall"
	"time"
)

const coreVersion = "v1.3.1"
const coreCommit = "5d7f5b11fcf231f72a0ec0d888bf0f2eadcce1da"

//go:embed licenses/AGPL-3.0.txt
var licenses embed.FS

func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func coreNotices(dir string) error {
	b, e := licenses.ReadFile("licenses/AGPL-3.0.txt")
	if e != nil {
		return e
	}
	if e = atomicWrite(filepath.Join(dir, "LICENSE"), b, 0600); e != nil {
		return e
	}
	source := fmt.Sprintf("zju-connect %s, unmodified upstream release\nLicense: GNU Affero General Public License version 3 (see LICENSE).\nUpstream source: https://github.com/Mythologyli/zju-connect/tree/%s\nSource archive: https://github.com/Mythologyli/zju-connect/archive/%s.tar.gz\nProvided WITHOUT ANY WARRANTY.\n", coreVersion, coreCommit, coreCommit)
	return atomicWrite(filepath.Join(dir, "SOURCE.txt"), []byte(source), 0600)
}
func installCore(ctx context.Context, root string) (string, error) {
	if value := os.Getenv("VPN_BINARY"); value != "" {
		p, e := filepath.Abs(value)
		if e != nil {
			return "", e
		}
		if s, e := os.Stat(p); e == nil && s.Mode().IsRegular() {
			return p, nil
		}
		return "", errors.New("配置的 VPN 核心不存在")
	}
	dir := filepath.Join(root, "vpn/bin")
	binary := filepath.Join(dir, "zju-connect")
	provenance := filepath.Join(dir, "release.json")
	var saved struct {
		Version    string `json:"version"`
		BinaryHash string `json:"binarySha256"`
	}
	b, _ := os.ReadFile(provenance)
	if json.Unmarshal(b, &saved) == nil && saved.Version == coreVersion {
		h, e := fileHash(binary)
		if e == nil && h == saved.BinaryHash {
			return binary, coreNotices(dir)
		}
	}
	client := NewClient("")
	client.Timeout = 2 * time.Minute
	defer client.CloseIdleConnections()
	r, e := get(ctx, client, "https://api.github.com/repos/Mythologyli/zju-connect/releases/tags/"+coreVersion)
	if e != nil {
		return "", errors.New("VPN 核心发布信息获取失败")
	}
	var release struct {
		Assets []struct {
			Name       string `json:"name"`
			Digest     string `json:"digest"`
			URL        string `json:"url"`
			BrowserURL string `json:"browser_download_url"`
		} `json:"assets"`
	}
	e = json.NewDecoder(io.LimitReader(r.Body, 4<<20)).Decode(&release)
	r.Body.Close()
	if e != nil {
		return "", errors.New("VPN 核心发布信息无效")
	}
	name := "zju-connect-" + runtime.GOOS + "-" + runtime.GOARCH + ".zip"
	for _, asset := range release.Assets {
		if asset.Name != name || !strings.HasPrefix(asset.Digest, "sha256:") {
			continue
		}
		req, e := http.NewRequestWithContext(ctx, "GET", asset.URL+"?download=1", nil)
		if e != nil {
			return "", e
		}
		req.Header.Set("User-Agent", "SEU-Monitor")
		req.Header.Set("Accept", "application/octet-stream")
		response, e := client.Do(req)
		if e != nil {
			return "", errors.New("VPN 核心下载失败")
		}
		data, e := io.ReadAll(io.LimitReader(response.Body, 30<<20+1))
		response.Body.Close()
		if e != nil || len(data) > 30<<20 || hash(data) != strings.TrimPrefix(asset.Digest, "sha256:") {
			return "", errors.New("VPN 核心下载校验失败")
		}
		zipped, e := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if e != nil {
			return "", errors.New("VPN 核心压缩包无效")
		}
		for _, file := range zipped.File {
			if filepath.Base(file.Name) != "zju-connect" {
				continue
			}
			if file.UncompressedSize64 > 100<<20 {
				return "", errors.New("VPN 核心文件过大")
			}
			stream, e := file.Open()
			if e != nil {
				return "", e
			}
			contents, e := io.ReadAll(io.LimitReader(stream, 100<<20+1))
			stream.Close()
			if e != nil || len(contents) > 100<<20 {
				return "", errors.New("VPN 核心解压失败")
			}
			if e = atomicWrite(binary, contents, 0700); e != nil {
				return "", e
			}
			b, _ := json.Marshal(map[string]string{"version": coreVersion, "source": asset.BrowserURL, "sourceCommit": coreCommit, "binarySha256": hash(contents)})
			if e = atomicWrite(provenance, b, 0600); e != nil {
				return "", e
			}
			return binary, coreNotices(dir)
		}
	}
	return "", errors.New("官方发布缺少适用平台或校验摘要，请设置 VPN_BINARY")
}
func coreArgs(binary string, port int, dir string) []string {
	return []string{binary, "-protocol", "atrust", "-server", "vpn.seu.edu.cn", "-port", "443", "-auth-type", "auth/cas", "-login-domain", "CAS-auth", "-disable-zju-config", "-remote-dns-server", getenv("VPN_DNS_SERVER", "202.119.24.12"), "-socks-bind", "", "-http-bind", fmt.Sprintf("127.0.0.1:%d", port), "-client-data-file", filepath.Join(dir, "client-data.json")}
}
func coreEnv() []string {
	out := []string{}
	debug := []string{}
	for _, v := range os.Environ() {
		k, value, _ := strings.Cut(v, "=")
		upper := strings.ToUpper(k)
		sensitive := false
		for _, word := range []string{"PASSWORD", "API_KEY", "TOKEN", "SECRET", "WEBHOOK"} {
			if strings.Contains(upper, word) {
				sensitive = true
			}
		}
		if sensitive || strings.HasPrefix(upper, "ZJU_CONNECT_") {
			continue
		}
		switch upper {
		case "VPN_USERNAME", "ATRUST_USERNAME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY":
			continue
		}
		if k == "GODEBUG" {
			for _, part := range strings.Split(value, ",") {
				if part != "" && !strings.HasPrefix(part, "tlsmlkem=") && !strings.HasPrefix(part, "tlssecpmlkem=") {
					debug = append(debug, part)
				}
			}
			continue
		}
		out = append(out, v)
	}
	debug = append(debug, "tlsmlkem=0", "tlssecpmlkem=0")
	return append(out, "GODEBUG="+strings.Join(debug, ","))
}
func coreLines(ctx context.Context, reader io.Reader) <-chan string {
	out := make(chan string, 64)
	go func() {
		defer close(out)
		r := bufio.NewReader(reader)
		var b strings.Builder
		for {
			ch, e := r.ReadByte()
			if e != nil {
				if b.Len() > 0 {
					select {
					case out <- b.String():
					case <-ctx.Done():
					}
				}
				return
			}
			b.WriteByte(ch)
			s := b.String()
			if ch == '\n' || strings.HasSuffix(s, ": ") && strings.Contains(s, "Please enter") {
				select {
				case out <- strings.TrimSpace(s):
				case <-ctx.Done():
					return
				}
				b.Reset()
			}
			if b.Len() > 65536 {
				b.Reset()
			}
		}
	}()
	return out
}
func RunVPN(ctx context.Context, root string, config Config, interactive bool) error {
	dir := filepath.Join(root, "vpn")
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	binary, e := installCore(ctx, root)
	if e != nil {
		return e
	}
	listener, e := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", config.Port))
	if e != nil {
		return errors.New("代理端口已被占用")
	}
	listener.Close()
	client := NewClient("")
	defer client.CloseIdleConnections()
	r, e := get(ctx, client, "https://vpn.seu.edu.cn/public/manifest")
	if e != nil {
		return errors.New("VPN 服务暂不可用")
	}
	var manifest map[string]any
	e = json.NewDecoder(r.Body).Decode(&manifest)
	r.Body.Close()
	if e != nil || numeric(manifest, "code") != 0 {
		return errors.New("VPN 服务暂不可用")
	}
	args := coreArgs(binary, config.Port, dir)
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Env = coreEnv()
	stdin, e := cmd.StdinPipe()
	if e != nil {
		return e
	}
	reader, writer := io.Pipe()
	cmd.Stdout = writer
	cmd.Stderr = writer
	if e = cmd.Start(); e != nil {
		reader.Close()
		writer.Close()
		return errors.New("VPN 核心启动失败")
	}
	done := make(chan error, 1)
	go func() { e := cmd.Wait(); writer.Close(); done <- e }()
	defer stopCore(cmd, stdin, reader, writer, done)
	lines := coreLines(ctx, reader)
	timeout := time.NewTimer(6 * time.Minute)
	defer timeout.Stop()
	ticker := time.NewTicker(15 * time.Minute)
	defer ticker.Stop()
	connected := false
	failures := 0
	proxy := fmt.Sprintf("http://127.0.0.1:%d", config.Port)
	loginPattern := regexp.MustCompile(`Visit (\S+) to login`)
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-timeout.C:
			if !connected {
				return errors.New("VPN 连接超时")
			}
		case <-ticker.C:
			if connected {
				if Probe(ctx, proxy) {
					failures = 0
				} else {
					failures++
					fmt.Printf("VPN 数据通道检查失败（%d/3）\n", failures)
					if failures >= 3 {
						return errors.New("VPN 数据通道连续 3 次检查失败")
					}
				}
			}
		case line, ok := <-lines:
			if !ok {
				return errors.New("zju-connect 已退出")
			}
			if strings.Contains(line, "VPN client setup error") {
				return errors.New("VPN 校园隧道建立失败")
			}
			if strings.Contains(line, "Login error") {
				return errors.New("VPN 登录失败，请检查账号密码或认证要求")
			}
			if match := loginPattern.FindStringSubmatch(line); len(match) > 0 {
				callback, e := loginCAS(ctx, config, dir, absolute("https://vpn.seu.edu.cn", match[1]), interactive)
				if e != nil {
					return e
				}
				if _, e = fmt.Fprintln(stdin, callback); e != nil {
					return errors.New("VPN 登录回调写入失败")
				}
			}
			if strings.Contains(line, "HTTP server listening") {
				ready := false
				for i := 0; i < 3; i++ {
					if Probe(ctx, proxy) {
						ready = true
						break
					}
					select {
					case <-ctx.Done():
						return nil
					case <-time.After(500 * time.Millisecond):
					}
				}
				if !ready {
					return errors.New("VPN 认证完成但数据通道检查失败")
				}
				connected = true
				ticker.Reset(15 * time.Minute)
				fmt.Printf("VPN 已连接，HTTP 代理：%s\n", proxy)
			}
			if strings.Contains(line, "Please enter") && !strings.Contains(strings.ToLower(line), "callback") {
				return errors.New("VPN 要求额外验证，请使用交互登录")
			}
		}
	}
}

// 先关闭输出管道，再等待子进程，避免未消费日志阻塞 exec.Wait。
func stopCore(cmd *exec.Cmd, stdin, reader, writer io.Closer, done <-chan error) {
	stdin.Close()
	reader.Close()
	writer.Close()
	cmd.Process.Signal(syscall.SIGTERM)
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		cmd.Process.Kill()
		<-done
	}
}
