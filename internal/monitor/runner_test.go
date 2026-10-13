package monitor

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRetryReuseDedupAndChangedAttachment(t *testing.T) {
	root := t.TempDir()
	var server *httptest.Server
	id := "notice123456"
	title := "测试通知"
	pdf := "first PDF"
	asset := "a.pdf"
	pushOK := false
	downloads, pushes := 0, 0
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/list.htm":
			fmt.Fprintf(w, `<div id="wp_news_w1"><table><tr><td class="main"><a href="/2026/%s/page.htm" title="%s">通知</a></td><td>2026-10-13</td></tr></table></div>`, id, title)
		case strings.HasSuffix(r.URL.Path, "/page.htm"):
			fmt.Fprintf(w, `<div class="wp_articlecontent"><p><span>测</span><span>试正文</span></p><a href="/%s">附件</a></div>`, asset)
		case strings.HasSuffix(r.URL.Path, ".pdf"):
			downloads++
			w.Header().Set("Content-Type", "application/pdf")
			fmt.Fprint(w, pdf)
		case r.URL.Path == "/hook":
			pushes++
			if r.Header.Get("Content-Type") != "application/json" {
				t.Error("missing JSON header")
			}
			code := 1
			if pushOK {
				code = 0
			}
			fmt.Fprintf(w, `{"code":%d}`, code)
		default:
			w.WriteHeader(404)
		}
	}))
	defer server.Close()
	old := Columns
	Columns = []Column{{"jwxx", "教务信息", server.URL + "/list.htm"}}
	defer func() { Columns = old }()
	c := DefaultConfig()
	c.Webhook = server.URL + "/hook"
	if n, e := Scan(context.Background(), root, c, false); e != nil || n != 0 {
		t.Fatal(n, e)
	}
	if downloads != 1 || pushes != 1 {
		t.Fatal(downloads, pushes)
	}
	pushOK = true
	if n, e := Scan(context.Background(), root, c, false); e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if downloads != 1 || pushes != 2 {
		t.Fatal("retry failed to reuse", downloads, pushes)
	}
	before := downloads
	if n, e := Scan(context.Background(), root, c, false); e != nil || n != 0 || downloads != before {
		t.Fatal("seen ID was fetched", n, e, downloads)
	}
	var duplicateLog bytes.Buffer
	previousLog := log.Writer()
	log.SetOutput(&duplicateLog)
	defer log.SetOutput(previousLog)
	id = "replacement123456"
	if n, e := Scan(context.Background(), root, c, false); e != nil || n != 0 {
		t.Fatal(n, e)
	}
	log.SetOutput(previousLog)
	if !strings.Contains(duplicateLog.String(), "跳过重复内容") || strings.Contains(duplicateLog.String(), "处理失败") {
		t.Fatal("duplicate reported as failure:", duplicateLog.String())
	}
	if pushes != 2 {
		t.Fatal("republished content pushed")
	}
	if !seenIDs(root, "教务信息")[id] {
		t.Fatal("duplicate ID not marked")
	}
	paths, _ := filepath.Glob(filepath.Join(root, "web", "教务处", "教务信息", "*", "meta.json"))
	if len(paths) != 1 {
		t.Fatal("duplicate retained", paths)
	}
	id = "changed123456"
	asset = "b.pdf"
	pdf = "changed PDF"
	if n, e := Scan(context.Background(), root, c, false); e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if pushes != 3 {
		t.Fatal("changed attachment suppressed")
	}
	id = "dryrun123456"
	if _, e := Scan(context.Background(), root, c, true); e != nil {
		t.Fatal(e)
	}
	if seenIDs(root, "教务信息")[id] || pushes != 3 {
		t.Fatal("dry run mutated state")
	}
}
func TestFailedHTMLAttachmentRetries(t *testing.T) {
	root := t.TempDir()
	n := Notice{SiteID: "jwc", ColumnID: "jwxx", ID: "notice123456", Title: "通知", Date: "2026-10-13"}
	pdfOK := false
	goodDownloads, badDownloads := 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/notice":
			fmt.Fprint(w, `<div class="wp_articlecontent"><p>正文</p><a href="/good.pdf">附件</a><a href="/bad.pdf">附件</a></div>`)
		case "/good.pdf":
			goodDownloads++
			w.Header().Set("Content-Type", "application/pdf")
			fmt.Fprint(w, "PDF")
		case "/bad.pdf":
			badDownloads++
			if !pdfOK {
				w.Header().Set("Content-Type", "text/html")
				fmt.Fprint(w, "登录页面")
			} else {
				w.Header().Set("Content-Type", "application/pdf")
				fmt.Fprint(w, "PDF2")
			}
		}
	}))
	defer server.Close()
	n.URL = server.URL + "/notice"
	client := NewClient("")
	defer client.CloseIdleConnections()
	c := DefaultConfig()
	seen := map[string]bool{}
	index := contentIndex{}
	if e := processNotice(context.Background(), root, c, client, index, seen, n, false); e == nil {
		t.Fatal("failed attachment delivered")
	}
	if seen[n.ID] {
		t.Fatal("failed ID marked")
	}
	pdfOK = true
	if e := processNotice(context.Background(), root, c, client, index, seen, n, false); e != nil {
		t.Fatal(e)
	}
	if goodDownloads != 1 || badDownloads != 2 {
		t.Fatal("partial success not reused", goodDownloads, badDownloads)
	}
}
func TestPythonMetadataReuseAndFingerprint(t *testing.T) {
	root := t.TempDir()
	n := Notice{SiteID: "jwc", ColumnID: "jwxx", ID: "old123456", Title: "旧通知", URL: "https://example.com/old", Date: "2026-10-13"}
	dir := directory(root, n)
	os.MkdirAll(filepath.Join(dir, "attachments"), 0700)
	data := []byte("legacy PDF")
	os.WriteFile(filepath.Join(dir, "attachments/a.pdf"), data, 0600)
	m := Metadata{Notice: n, Attachments: []Attachment{{URL: "https://example.com/a.pdf", Filename: "a.pdf", SHA256: hash(data), ContentType: "application/pdf"}}}
	b, _ := json.Marshal(m)
	os.WriteFile(filepath.Join(dir, "meta.json"), b, 0600)
	os.WriteFile(filepath.Join(dir, "text.md"), []byte("# 旧通知\n\n抓取时间：2026-10-13\n\n旧\n正文\n"), 0600)
	if e := markSeen(root, "教务信息", n.ID); e != nil {
		t.Fatal(e)
	}
	if len(reusable(dir)) != 1 {
		t.Fatal("legacy attachment not reused")
	}
	index := loadIndex(root)
	updated := m
	updated.ID = "new123456"
	if index.find(updated, "旧正文") != dir {
		t.Fatal("legacy content not recognized")
	}
	os.WriteFile(filepath.Join(dir, "attachments/a.pdf"), []byte("corrupt"), 0600)
	if len(reusable(dir)) != 0 || len(loadIndex(root)) != 0 {
		t.Fatal("corrupted attachment trusted")
	}
}

func TestFailedColumnDoesNotBlockOtherColumns(t *testing.T) {
	root := t.TempDir()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/broken":
			w.WriteHeader(503)
		case "/list":
			fmt.Fprint(w, `<div id="wp_news_w1"><table><tr><td class="main"><a href="/2026/notice123456/page.htm">通知</a></td><td>2026-10-13</td></tr></table></div>`)
		default:
			fmt.Fprint(w, `<div class="wp_articlecontent"><p>正文</p></div>`)
		}
	}))
	defer server.Close()
	old := Columns
	Columns = []Column{{"zxdt", "最新动态", server.URL + "/broken"}, {"jwxx", "教务信息", server.URL + "/list"}}
	defer func() { Columns = old }()
	n, e := Scan(context.Background(), root, DefaultConfig(), false)
	if e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if !seenIDs(root, "教务信息")["notice123456"] {
		t.Fatal("remaining column not processed")
	}
}

func TestAllFailedColumnsReturnError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusForbidden) }))
	defer server.Close()
	old := Columns
	Columns = []Column{{"zxdt", "最新动态", server.URL + "/news"}, {"jwxx", "教务信息", server.URL + "/academic"}}
	defer func() { Columns = old }()
	for _, dry := range []bool{false, true} {
		n, err := Scan(context.Background(), t.TempDir(), DefaultConfig(), dry)
		if n != 0 || err == nil || !strings.Contains(err.Error(), "所有栏目抓取失败") || !strings.Contains(err.Error(), "最新动态") || !strings.Contains(err.Error(), "教务信息") {
			t.Fatalf("count=%d error=%v", n, err)
		}
	}
}

func TestEmptyColumnIsSuccessful(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/broken" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		fmt.Fprint(w, `<div id="wp_news_w1"><table></table></div>`)
	}))
	defer server.Close()
	old := Columns
	Columns = []Column{{"zxdt", "最新动态", server.URL + "/broken"}, {"jwxx", "教务信息", server.URL + "/empty"}}
	defer func() { Columns = old }()
	if n, err := Scan(context.Background(), t.TempDir(), DefaultConfig(), false); n != 0 || err != nil {
		t.Fatal(n, err)
	}
}
