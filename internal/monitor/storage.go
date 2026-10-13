package monitor

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"golang.org/x/text/unicode/norm"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
)

func hash(b []byte) string { sum := sha256.Sum256(b); return hex.EncodeToString(sum[:]) }
func fileHash(path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	_, e = io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil)), e
}

var invalidPath = regexp.MustCompile(`[/:*?"<>|]`)

func cleanName(s string) string { return invalidPath.ReplaceAllString(s, "_") }
func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
func directory(root string, n Notice) string {
	parent := filepath.Join(root, "web", "教务处", cleanName(columnName(n.ColumnID)))
	suffix := "--" + hash([]byte(n.URL))[:16]
	entries, _ := os.ReadDir(parent)
	for _, entry := range entries {
		if entry.IsDir() && strings.HasSuffix(entry.Name(), suffix) {
			return filepath.Join(parent, entry.Name())
		}
	}
	return filepath.Join(parent, truncate(cleanName(n.Title), 60)+suffix)
}
func readMeta(dir string) (Metadata, error) {
	var m Metadata
	b, e := os.ReadFile(filepath.Join(dir, "meta.json"))
	if e != nil {
		return m, e
	}
	e = json.Unmarshal(b, &m)
	return m, e
}
func reusable(dir string) map[string]Attachment {
	out := map[string]Attachment{}
	m, e := readMeta(dir)
	if e != nil {
		return out
	}
	for _, a := range m.Attachments {
		if a.Error != "" || a.Filename == "" || filepath.Base(a.Filename) != a.Filename {
			continue
		}
		h, e := fileHash(filepath.Join(dir, "attachments", a.Filename))
		if e == nil && h == a.SHA256 {
			out[a.URL] = a
		}
	}
	return out
}
func escapePath(s string) string {
	var b strings.Builder
	for _, c := range []byte(s) {
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("-_.~", rune(c)) {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}
func SaveSnapshot(dir string, n Notice, d Detail, attachments []Attachment) (Metadata, error) {
	now := time.Now().In(time.FixedZone("CST", 8*3600)).Format("2006-01-02T15:04:05+08:00")
	body := d.Markdown
	if body == "" {
		body = d.Text
	}
	for _, a := range attachments {
		if a.Error == "" && a.Filename != "" {
			body = strings.ReplaceAll(body, "(<"+a.URL+">)", "(<attachments/"+escapePath(a.Filename)+">)")
		}
	}
	text := fmt.Sprintf("# %s\n\n来源：%s\n站点：%s\n栏目：%s\n发布时间：%s\n抓取时间：%s\n\n%s\n", n.Title, n.URL, n.SiteID, n.ColumnID, n.Date, now, body)
	raw := d.RawHTML
	if raw == "" {
		raw = d.HTML
	}
	if attachments == nil {
		attachments = []Attachment{}
	}
	m := Metadata{Notice: n, FetchedAt: now, HTMLHash: hash([]byte(raw)), TextHash: hash([]byte(text)), ContentVersion: 1, ContentText: &d.Text, SnapshotPath: dir, Attachments: attachments}
	if e := atomicWrite(filepath.Join(dir, "text.md"), []byte(text), 0600); e != nil {
		return m, e
	}
	b, e := json.MarshalIndent(m, "", "  ")
	if e != nil {
		return m, e
	}
	if e = atomicWrite(filepath.Join(dir, "meta.json"), b, 0600); e != nil {
		return m, e
	}
	for _, name := range []string{"raw.html", "raw.html.tmp"} {
		os.Remove(filepath.Join(dir, name))
	}
	return m, nil
}
func seenIDs(root, name string) map[string]bool {
	out := map[string]bool{}
	b, _ := os.ReadFile(filepath.Join(root, "state", name, "sent_ids.txt"))
	for _, v := range strings.Split(string(b), "\n") {
		v = strings.TrimSpace(v)
		if len(v) > 5 {
			out[v] = true
		}
	}
	return out
}
func markSeen(root, name, id string) error {
	ids := seenIDs(root, name)
	ids[id] = true
	keys := []string{}
	for k := range ids {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return atomicWrite(filepath.Join(root, "state", name, "sent_ids.txt"), []byte(strings.Join(keys, "\n")+"\n"), 0600)
}
func normalize(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return r
	}, norm.NFKC.String(s))
}

type contentRecord struct {
	Metadata  Metadata
	Directory string
	Images    []string
}
type contentIndex map[string][]contentRecord

func signature(m Metadata, text string) (string, []string, error) {
	files, images := map[string]bool{}, map[string]bool{}
	for _, a := range m.Attachments {
		if a.Error != "" || a.SHA256 == "" {
			return "", nil, errors.New("附件不完整")
		}
		if imageFile(a.Filename, a.ContentType) {
			images[a.SHA256] = true
		} else {
			files[a.SHA256] = true
		}
	}
	keys := func(m map[string]bool) []string {
		v := []string{}
		for k := range m {
			v = append(v, k)
		}
		sort.Strings(v)
		return v
	}
	body := normalize(text)
	if body == "" && len(files) == 0 && len(images) == 0 {
		return "", nil, errors.New("空正文")
	}
	// 摘要仅用于本轮比较，不落盘；Python 元数据和状态文件可直接复用。
	b, _ := json.Marshal([]any{m.SiteID, normalize(m.Title), m.Date, body, keys(files)})
	return hash(b), keys(images), nil
}

var legacyImages = regexp.MustCompile(`!\[[^\]]*\]\([^\n]*?\)`)

func metadataText(m Metadata, dir string) (string, error) {
	if m.ContentText != nil {
		return *m.ContentText, nil
	}
	b, e := os.ReadFile(filepath.Join(dir, "text.md"))
	if e != nil {
		return "", e
	}
	marker := strings.Index(string(b), "抓取时间：")
	if marker < 0 {
		return "", errors.New("缺少正文边界")
	}
	rest := string(b)[marker:]
	_, body, ok := strings.Cut(rest, "\n\n")
	if !ok {
		return "", errors.New("缺少正文边界")
	}
	return legacyImages.ReplaceAllString(body, ""), nil
}
func (index contentIndex) add(m Metadata, dir, text string) {
	key, images, e := signature(m, text)
	if e == nil {
		index[key] = append(index[key], contentRecord{m, dir, images})
	}
}
func (index contentIndex) find(m Metadata, text string) string {
	key, images, e := signature(m, text)
	if e != nil {
		return ""
	}
	for _, r := range index[key] {
		legacy := r.Metadata.ContentVersion == 0 && len(r.Images) == 0 && len(r.Metadata.Attachments) > 0
		if strings.Join(images, ",") == strings.Join(r.Images, ",") || legacy {
			return r.Directory
		}
	}
	return ""
}
func loadIndex(root string) contentIndex {
	index := contentIndex{}
	records := []contentRecord{}
	for _, c := range Columns {
		seen := seenIDs(root, c.Name)
		paths, _ := filepath.Glob(filepath.Join(root, "web", "教务处", c.Name, "*", "meta.json"))
		for _, p := range paths {
			dir := filepath.Dir(p)
			m, e := readMeta(dir)
			if e != nil || !seen[m.ID] {
				continue
			}
			valid := true
			for _, a := range m.Attachments {
				h, e := fileHash(filepath.Join(dir, "attachments", a.Filename))
				if a.Error != "" || e != nil || h != a.SHA256 {
					valid = false
					break
				}
			}
			if valid {
				records = append(records, contentRecord{Metadata: m, Directory: dir})
			}
		}
	}
	sort.Slice(records, func(i, j int) bool { return records[i].Metadata.FetchedAt < records[j].Metadata.FetchedAt })
	for _, r := range records {
		text, e := metadataText(r.Metadata, r.Directory)
		if e == nil {
			index.add(r.Metadata, r.Directory, text)
		}
	}
	return index
}
