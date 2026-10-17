package monitor

import (
	"context"
	"errors"
	"fmt"
	"golang.org/x/net/html/charset"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"time"
)

const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

func NewClient(proxy string) *http.Client {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.Proxy = nil
	if proxy != "" {
		p, e := url.Parse(proxy)
		if e == nil {
			t.Proxy = http.ProxyURL(p)
		}
	}
	jar, _ := cookiejar.New(nil)
	return &http.Client{Transport: t, Jar: jar, Timeout: 20 * time.Second}
}
func request(ctx context.Context, c *http.Client, method, u string, body io.Reader) (*http.Response, error) {
	r, e := http.NewRequestWithContext(ctx, method, u, body)
	if e != nil {
		return nil, errors.New("请求地址无效")
	}
	r.Header.Set("User-Agent", userAgent)
	if method == "POST" {
		r.Header.Set("Content-Type", "application/json")
	}
	return c.Do(r)
}
func get(ctx context.Context, c *http.Client, u string) (*http.Response, error) {
	var last error
	for i := 0; i < 2; i++ {
		r, e := request(ctx, c, "GET", u, nil)
		if e == nil && r.StatusCode >= 200 && r.StatusCode < 400 {
			return r, nil
		}
		retry := e != nil
		if r != nil {
			retry = r.StatusCode == 502 || r.StatusCode == 503 || r.StatusCode == 504
			last = fmt.Errorf("HTTP %d", r.StatusCode)
			r.Body.Close()
		} else {
			last = errors.New("网络连接失败")
		}
		if !retry || i == 1 {
			return nil, last
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return nil, last
}
func page(ctx context.Context, c *http.Client, u string) (string, error) {
	r, e := get(ctx, c, u)
	if e != nil {
		return "", e
	}
	defer r.Body.Close()
	reader, e := charset.NewReader(r.Body, r.Header.Get("Content-Type"))
	if e != nil {
		return "", e
	}
	b, e := io.ReadAll(io.LimitReader(reader, 16<<20))
	return string(b), e
}
func Probe(ctx context.Context, proxy string) bool {
	c := NewClient(proxy)
	// 探测站点能否响应；页面跳转不代表隧道故障。
	c.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	c.Timeout = 10 * time.Second
	defer c.CloseIdleConnections()
	u := "https://cvs.seu.edu.cn"
	if v := getenv("VPN_CHECK_URL", ""); v != "" {
		u = v
	}
	r, e := request(ctx, c, "GET", u, nil)
	if e != nil {
		return false
	}
	defer r.Body.Close()
	return r.StatusCode < 500
}
