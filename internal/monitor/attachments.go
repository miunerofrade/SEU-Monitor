package monitor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
)

func attachmentFilename(r *http.Response, c Candidate, i int) string {
	name := ""
	_, params, e := mime.ParseMediaType(r.Header.Get("Content-Disposition"))
	if e == nil {
		name = params["filename"]
	}
	if name == "" {
		u, _ := url.Parse(c.URL)
		last := path.Base(u.Path)
		if strings.Contains(last, ".") {
			name = last
		} else if c.Text != "" && c.Text != last {
			name = c.Text
		}
	}
	if name == "" {
		name = fmt.Sprintf("attachment_%d%s", i, fileSuffix(c.URL))
	}
	name = strings.ReplaceAll(cleanName(name), "\\", "_")
	name = strings.TrimSpace(name)
	if name == "" {
		name = "unnamed"
	}
	if len([]rune(name)) > 200 {
		ext := path.Ext(name)
		name = truncate(strings.TrimSuffix(name, ext), 196) + ext
	}
	ext := path.Ext(name)
	return strings.TrimSuffix(name, ext) + "--" + hash([]byte(c.URL))[:10] + ext
}
func downloadAttachment(ctx context.Context, client *http.Client, c Candidate, target string, i int) Attachment {
	a := Attachment{URL: c.URL}
	ctx, cancel := context.WithTimeout(ctx, 15_000_000_000)
	defer cancel()
	r, e := get(ctx, client, c.URL)
	if e != nil {
		a.Error = e.Error()
		return a
	}
	defer r.Body.Close()
	ct, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if ct == "text/html" || ct == "application/xhtml+xml" {
		a.Error = "跳过：响应为 text/html，不是附件原文件"
		return a
	}
	if e = os.MkdirAll(target, 0700); e != nil {
		a.Error = "无法创建附件目录"
		return a
	}
	a.Filename = attachmentFilename(r, c, i)
	f, e := os.CreateTemp(target, ".attachment-*")
	if e != nil {
		a.Error = "无法创建临时附件"
		return a
	}
	defer os.Remove(f.Name())
	h := sha256.New()
	size, e := io.Copy(io.MultiWriter(f, h), r.Body)
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e == nil {
		e = os.Rename(f.Name(), filepath.Join(target, a.Filename))
	}
	if e != nil {
		a.Error = "附件下载或保存失败"
		return a
	}
	a.SHA256 = hex.EncodeToString(h.Sum(nil))
	a.Size = size
	a.ContentType = r.Header.Get("Content-Type")
	return a
}
func DownloadAttachments(ctx context.Context, c *http.Client, candidates []Candidate, dir string, previous map[string]Attachment) []Attachment {
	out := []Attachment{}
	seen := map[string]bool{}
	for i, candidate := range candidates {
		if seen[candidate.URL] || !IsCandidate(candidate) {
			continue
		}
		seen[candidate.URL] = true
		a, ok := previous[candidate.URL]
		if !ok || a.Error != "" {
			a = downloadAttachment(ctx, c, candidate, dir, i+1)
		}
		out = append(out, a)
	}
	return out
}
