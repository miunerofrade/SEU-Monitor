package monitor

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestProbeResponseStatus(t *testing.T) {
	for _, status := range []int{200, 301, 302, 401, 403, 500, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			requests := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				if status >= 300 && status < 400 {
					w.Header().Set("Location", "/")
				}
				w.WriteHeader(status)
			}))
			defer server.Close()
			t.Setenv("VPN_CHECK_URL", server.URL)
			if got := Probe(context.Background(), ""); got != (status < 500) {
				t.Fatalf("status %d: healthy=%v", status, got)
			}
			if requests != 1 {
				t.Fatalf("probe followed redirects: %d requests", requests)
			}
		})
	}
}

func TestProbeCannotConnect(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	server.Close()
	t.Setenv("VPN_CHECK_URL", server.URL)
	if Probe(context.Background(), "") {
		t.Fatal("unreachable endpoint reported healthy")
	}
}

func TestProbeThroughProxy(t *testing.T) {
	t.Setenv("VPN_CHECK_URL", "http://campus.invalid/")
	requests := 0
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Host != "campus.invalid" {
			t.Errorf("unexpected target %s", r.URL.Host)
		}
		w.Header().Set("Location", "http://campus.invalid/")
		w.WriteHeader(http.StatusMovedPermanently)
	}))
	defer proxy.Close()
	if !Probe(context.Background(), proxy.URL) || requests != 1 {
		t.Fatalf("proxy probe failed or followed redirects: requests=%d", requests)
	}
}
