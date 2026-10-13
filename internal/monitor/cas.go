package monitor

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"golang.org/x/term"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const authRoot = "https://auth.seu.edu.cn/auth/casback"

func campusURL(value string) error {
	u, e := url.Parse(value)
	if e != nil || u.Scheme != "https" || (u.Hostname() != "auth.seu.edu.cn" && u.Hostname() != "vpn.seu.edu.cn") || u.User != nil || (u.Port() != "" && u.Port() != "443") {
		return errors.New("认证跳转地址不属于学校认证系统")
	}
	return nil
}
func validateCallback(value string) (string, error) {
	u, e := url.Parse(value)
	if e != nil {
		return "", errors.New("VPN 回调地址无效")
	}
	q := u.Query()
	if campusURL(value) != nil || u.Hostname() != "vpn.seu.edu.cn" || u.Path != "/passport/v1/auth/cas" || len(q["sfDomain"]) != 1 || q.Get("sfDomain") != "CAS-auth" || q.Get("ticket") == "" {
		return "", errors.New("VPN 回调地址或票据不匹配")
	}
	u.Host = "vpn.seu.edu.cn"
	u.Fragment = ""
	return u.String(), nil
}
func fingerprint(dir string) (string, error) {
	p := filepath.Join(dir, "device.json")
	if s, e := os.Lstat(p); e == nil && s.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("设备标识不能是符号链接")
	}
	b, e := os.ReadFile(p)
	if e == nil {
		var value struct {
			Fingerprint string `json:"fingerprint"`
		}
		if json.Unmarshal(b, &value) != nil || !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(value.Fingerprint) {
			return "", errors.New("设备标识无效")
		}
		return value.Fingerprint, nil
	}
	if !os.IsNotExist(e) {
		return "", e
	}
	data := make([]byte, 16)
	if _, e = rand.Read(data); e != nil {
		return "", e
	}
	value := hex.EncodeToString(data)
	b, _ = json.Marshal(map[string]string{"fingerprint": value})
	f, e := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return "", e
	}
	_, e = f.Write(b)
	ce := f.Close()
	if e == nil {
		e = ce
	}
	return value, e
}
func readSecret(prompt string) (string, error) {
	if !term.IsTerminal(int(os.Stdin.Fd())) {
		return "", errors.New("需要交互终端输入密码或验证码")
	}
	fmt.Print(prompt)
	b, e := term.ReadPassword(int(os.Stdin.Fd()))
	fmt.Println()
	return strings.TrimSpace(string(b)), e
}
func rsaKey(encoded string) (*rsa.PublicKey, error) {
	b, e := base64.RawURLEncoding.DecodeString(strings.TrimRight(encoded, "="))
	if e != nil {
		b, e = base64.StdEncoding.DecodeString(encoded)
	}
	if e != nil {
		return nil, errors.New("校园公钥无效")
	}
	key, e := x509.ParsePKIXPublicKey(b)
	if e == nil {
		if r, ok := key.(*rsa.PublicKey); ok {
			return r, nil
		}
	}
	if r, e := x509.ParsePKCS1PublicKey(b); e == nil {
		return r, nil
	}
	return nil, errors.New("校园公钥格式无效")
}
func encrypt(key *rsa.PublicKey, value string) (string, error) {
	b, e := rsa.EncryptPKCS1v15(rand.Reader, key, []byte(value))
	return base64.StdEncoding.EncodeToString(b), e
}

type casLogin struct {
	ctx         context.Context
	client      *http.Client
	config      Config
	dir         string
	interactive bool
	root        string
	prompt      func(string) (string, error)
}

func (c *casLogin) post(endpoint string, payload any) (map[string]any, error) {
	b, _ := json.Marshal(payload)
	r, e := http.NewRequestWithContext(c.ctx, "POST", c.root+"/"+endpoint, bytes.NewReader(b))
	if e != nil {
		return nil, errors.New("认证请求无效")
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("User-Agent", userAgent)
	r.Header.Set("Origin", "https://auth.seu.edu.cn")
	r.Header.Set("Referer", "https://auth.seu.edu.cn/dist/")
	response, e := c.client.Do(r)
	if e != nil {
		return nil, errors.New("校园认证请求失败")
	}
	defer response.Body.Close()
	if response.StatusCode >= 400 {
		return nil, errors.New("校园认证接口失败")
	}
	var result map[string]any
	if json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result) != nil || result == nil {
		return nil, errors.New("校园认证返回无效数据")
	}
	return result, nil
}
func (c *casLogin) follow(value string) (string, bool, error) {
	for i := 0; i < 20; i++ {
		if c.ctx.Err() != nil {
			return "", false, c.ctx.Err()
		}
		if e := campusURL(value); e != nil {
			return "", false, e
		}
		u, _ := url.Parse(value)
		if u.Hostname() == "vpn.seu.edu.cn" && u.Path == "/passport/v1/auth/cas" && u.Query().Get("ticket") != "" {
			return value, true, nil
		}
		r, e := request(c.ctx, c.client, "GET", value, nil)
		if e != nil {
			return "", false, errors.New("校园认证跳转失败")
		}
		r.Body.Close()
		switch r.StatusCode {
		case 301, 302, 303, 307, 308:
			location := r.Header.Get("Location")
			if location == "" {
				return "", false, errors.New("认证跳转缺少地址")
			}
			value = absolute(value, location)
			continue
		}
		if r.StatusCode >= 400 {
			return "", false, errors.New("校园认证页面不可用")
		}
		return value, false, nil
	}
	return "", false, errors.New("校园认证跳转次数过多")
}
func numeric(m map[string]any, key string) int { v, _ := m[key].(float64); return int(v) }
func (c *casLogin) key() (*rsa.PublicKey, error) {
	m, e := c.post("getChiperKey", map[string]any{})
	if e != nil {
		return nil, e
	}
	encoded, _ := m["publicKey"].(string)
	return rsaKey(encoded)
}
func (c *casLogin) authenticate(entry string) (string, error) {
	submitted := false
	for i := 0; i < 3; i++ {
		target, callback, e := c.follow(entry)
		if e != nil {
			return "", e
		}
		if callback {
			return validateCallback(target)
		}
		u, _ := url.Parse(target)
		if u.Hostname() != "auth.seu.edu.cn" {
			return "", errors.New("学校没有返回 CAS 认证地址")
		}
		query := u.RawQuery
		if query == "" {
			_, query, _ = strings.Cut(u.Fragment, "?")
		}
		q, _ := url.ParseQuery(query)
		service := q.Get("service")
		if e = campusURL(service); e != nil {
			return "", e
		}
		result, e := c.post("verifyTgt", map[string]any{"service": service, "loginType": "account"})
		if e != nil {
			return "", e
		}
		success, _ := result["success"].(bool)
		passwordLogin := !success
		if passwordLogin {
			if submitted {
				return "", errors.New("认证会话未保持，请重试登录")
			}
			if c.config.Account == "" || c.config.Password == "" {
				return "", errors.New("请先配置校园账号密码")
			}
			r, e := request(c.ctx, c.client, "GET", c.root+"/needCaptcha", nil)
			if e != nil {
				return "", errors.New("校园验证码检查失败")
			}
			var captcha map[string]any
			decodeErr := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&captcha)
			r.Body.Close()
			if r.StatusCode >= 400 || decodeErr != nil {
				return "", errors.New("校园验证码检查失败")
			}
			if numeric(captcha, "code") == 4000 {
				return "", errors.New("学校要求图形验证码；当前 HTTP 登录暂不支持，请稍后重试")
			}
			key, e := c.key()
			if e != nil {
				return "", e
			}
			password, e := encrypt(key, c.config.Password)
			if e != nil {
				return "", errors.New("密码加密失败")
			}
			fp, e := fingerprint(c.dir)
			if e != nil {
				return "", e
			}
			payload := map[string]any{"service": service, "username": c.config.Account, "password": password, "captcha": "", "rememberMe": false, "loginType": "account", "wxBinded": false, "mobilePhoneNum": "", "mobileVerifyCode": "", "fingerPrint": fp}
			result, e = c.post("casLogin", payload)
			if e != nil {
				return "", e
			}
			if numeric(result, "code") == 502 {
				if !c.interactive {
					return "", errors.New("学校要求短信验证，请运行 monitor vpn --interactive")
				}
				sent, e := c.post("sendStage2Code", map[string]any{"userId": c.config.Account})
				if e != nil || numeric(sent, "code") != 200 {
					return "", errors.New("短信发送失败")
				}
				fmt.Println("短信验证码已发送到校园账号绑定手机")
				code, e := c.prompt("短信验证码：")
				if e != nil {
					return "", e
				}
				if !regexp.MustCompile(`^[0-9]{4,16}$`).MatchString(code) {
					return "", errors.New("请输入短信中的数字验证码")
				}
				key, e = c.key()
				if e != nil {
					return "", e
				}
				payload["password"], e = encrypt(key, c.config.Password)
				if e != nil {
					return "", e
				}
				payload["mobileVerifyCode"], e = encrypt(key, code)
				if e != nil {
					return "", e
				}
				result, e = c.post("casLogin", payload)
				if e != nil {
					return "", e
				}
			}
			submitted = true
			if numeric(result, "code") != 200 {
				return "", errors.New("校园认证未通过，请检查账号、密码或验证码")
			}
		}
		redirect, _ := result["redirectUrl"].(string)
		if redirect == "" {
			return "", errors.New("校园认证未返回业务跳转地址")
		}
		if passwordLogin {
			redirect = c.root + "/loginRedirect?redirectUrl=" + redirect
		}
		target, callback, e = c.follow(redirect)
		if e != nil {
			return "", e
		}
		if callback {
			return validateCallback(target)
		}
	}
	return "", errors.New("校园 VPN 认证回调未建立")
}
func loginCAS(ctx context.Context, config Config, dir, entry string, interactive bool) (string, error) {
	client := NewClient("")
	defer client.CloseIdleConnections()
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	c := casLogin{ctx, client, config, dir, interactive, authRoot, func(prompt string) (string, error) { return secretContext(ctx, prompt) }}
	return c.authenticate(entry)
}

func secretContext(ctx context.Context, prompt string) (string, error) {
	state, e := term.GetState(int(os.Stdin.Fd()))
	if e != nil {
		return "", errors.New("需要交互终端输入验证码")
	}
	type result struct {
		value string
		err   error
	}
	done := make(chan result, 1)
	go func() { value, e := readSecret(prompt); done <- result{value, e} }()
	select {
	case <-ctx.Done():
		term.Restore(int(os.Stdin.Fd()), state)
		return "", ctx.Err()
	case value := <-done:
		return value.value, value.err
	}
}
