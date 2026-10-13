package monitor

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func response(r *http.Request, status int, body string, headers http.Header) *http.Response {
	if headers == nil {
		headers = http.Header{}
	}
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body)), Header: headers, Request: r}
}
func TestCASPasswordAndSMSFlowDoesNotConsumeTicket(t *testing.T) {
	for _, sms := range []bool{false, true} {
		t.Run(map[bool]string{false: "password", true: "sms"}[sms], func(t *testing.T) {
			key, e := rsa.GenerateKey(rand.Reader, 2048)
			if e != nil {
				t.Fatal(e)
			}
			der, _ := x509.MarshalPKIXPublicKey(&key.PublicKey)
			public := base64.RawURLEncoding.EncodeToString(der)
			keys, logins := 0, 0
			consumed := false
			callback := "https://vpn.seu.edu.cn/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=ST-test"
			service := "https://vpn.seu.edu.cn/passport/v1/auth/cas?sfDomain=CAS-auth"
			client := NewClient("")
			client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
			client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
				switch r.URL.Path {
				case "/entry":
					return response(r, 302, "", http.Header{"Location": []string{"https://auth.seu.edu.cn/login?service=" + url.QueryEscape(service)}}), nil
				case "/login":
					return response(r, 200, "login", nil), nil
				case "/auth/casback/verifyTgt":
					return response(r, 200, `{"success":false}`, nil), nil
				case "/auth/casback/needCaptcha":
					return response(r, 200, `{"code":200}`, nil), nil
				case "/auth/casback/getChiperKey":
					keys++
					return response(r, 200, `{"publicKey":"`+public+`"}`, nil), nil
				case "/auth/casback/sendStage2Code":
					return response(r, 200, `{"code":200}`, nil), nil
				case "/auth/casback/casLogin":
					logins++
					var payload map[string]any
					json.NewDecoder(r.Body).Decode(&payload)
					encrypted, _ := base64.StdEncoding.DecodeString(payload["password"].(string))
					plain, e := rsa.DecryptPKCS1v15(rand.Reader, key, encrypted)
					if e != nil || string(plain) != "password" {
						t.Error("password encryption mismatch")
					}
					if sms && logins == 1 {
						return response(r, 200, `{"code":502}`, nil), nil
					}
					if sms {
						data, _ := base64.StdEncoding.DecodeString(payload["mobileVerifyCode"].(string))
						code, e := rsa.DecryptPKCS1v15(rand.Reader, key, data)
						if e != nil || string(code) != "123456" {
							t.Error("SMS encryption mismatch")
						}
					}
					return response(r, 200, `{"code":200,"redirectUrl":"go-next"}`, nil), nil
				case "/auth/casback/loginRedirect":
					return response(r, 302, "", http.Header{"Location": []string{callback}}), nil
				case "/passport/v1/auth/cas":
					consumed = true
					return response(r, 500, "must not consume", nil), nil
				}
				t.Fatalf("unexpected request %s", r.URL.Path)
				return nil, nil
			})
			c := casLogin{context.Background(), client, Config{Account: "account", Password: "password"}, t.TempDir(), sms, authRoot, func(string) (string, error) { return "123456", nil }}
			got, e := c.authenticate("https://vpn.seu.edu.cn/entry")
			if e != nil || got != callback {
				t.Fatal(got, e)
			}
			if consumed {
				t.Fatal("CAS ticket consumed")
			}
			if sms && keys != 2 {
				t.Fatal("stage two did not fetch fresh key")
			}
		})
	}
}
func TestCallbackAndDeviceCompatibility(t *testing.T) {
	for _, value := range []string{"http://vpn.seu.edu.cn/passport/v1/auth/cas?ticket=x", "https://evil.example/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=x", "https://vpn.seu.edu.cn:444/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=x", "https://user:secret@vpn.seu.edu.cn/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=x"} {
		if _, e := validateCallback(value); e == nil {
			t.Fatal("unsafe callback accepted")
		}
	}
	valid, e := validateCallback("https://vpn.seu.edu.cn:443/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=x#ignored")
	if e != nil || strings.Contains(valid, ":443") || strings.Contains(valid, "#") {
		t.Fatal(valid, e)
	}
	dir := t.TempDir()
	a, e := fingerprint(dir)
	if e != nil {
		t.Fatal(e)
	}
	b, e := fingerprint(dir)
	if e != nil || a != b {
		t.Fatal("device not stable")
	}
	os.Remove(filepath.Join(dir, "device.json"))
	os.Symlink(filepath.Join(dir, "elsewhere"), filepath.Join(dir, "device.json"))
	if _, e = fingerprint(dir); e == nil {
		t.Fatal("symlink accepted")
	}
}
