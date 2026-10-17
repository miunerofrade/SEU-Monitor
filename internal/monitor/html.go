package monitor

import (
	"bytes"
	"golang.org/x/net/html"
	"net/url"
	"path"
	"regexp"
	"strings"
)

var spaces = regexp.MustCompile(`[\s\x{00a0}]+`)
var blankLines = regexp.MustCompile(`\n{3,}`)
var endSpaces = regexp.MustCompile(`[ \t]+\n`)
var startSpaces = regexp.MustCompile(`\n +`)

func attr(n *html.Node, k string) string {
	for _, a := range n.Attr {
		if a.Key == k {
			return a.Val
		}
	}
	return ""
}
func hasClass(n *html.Node, c string) bool {
	for _, v := range strings.Fields(attr(n, "class")) {
		if v == c {
			return true
		}
	}
	return false
}
func nodes(n *html.Node, predicate func(*html.Node) bool) []*html.Node {
	var out []*html.Node
	var visit func(*html.Node)
	visit = func(v *html.Node) {
		if predicate(v) {
			out = append(out, v)
		}
		for c := v.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	visit(n)
	return out
}
func first(n *html.Node, p func(*html.Node) bool) *html.Node {
	r := nodes(n, p)
	if len(r) > 0 {
		return r[0]
	}
	return nil
}
func tag(n *html.Node, t string) bool { return n.Type == html.ElementNode && n.Data == t }
func inlineText(n *html.Node) string {
	var b strings.Builder
	var walk func(*html.Node)
	walk = func(v *html.Node) {
		if v.Type == html.TextNode {
			b.WriteString(v.Data)
		}
		for c := v.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(n)
	return strings.TrimSpace(b.String())
}
func Text(n *html.Node) string {
	blocks := map[string]bool{}
	for _, t := range strings.Fields("p div article section blockquote li ul ol h1 h2 h3 h4 h5 h6 header footer table") {
		blocks[t] = true
	}
	var b strings.Builder
	var walk func(*html.Node)
	walk = func(v *html.Node) {
		if v.Type == html.CommentNode {
			return
		}
		if v.Type == html.TextNode {
			b.WriteString(spaces.ReplaceAllString(v.Data, " "))
			return
		}
		if v.Data == "script" || v.Data == "style" || v.Data == "noscript" {
			return
		}
		if v.Data == "br" {
			b.WriteByte('\n')
			return
		}
		if v.Data == "pre" {
			b.WriteString("\n\n" + inlineText(v) + "\n\n")
			return
		}
		if blocks[v.Data] {
			b.WriteString("\n\n")
		}
		for c := v.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
		if blocks[v.Data] {
			b.WriteString("\n\n")
		} else if v.Data == "tr" {
			b.WriteByte('\n')
		} else if v.Data == "td" || v.Data == "th" {
			b.WriteByte('\t')
		}
	}
	walk(n)
	text := endSpaces.ReplaceAllString(b.String(), "\n")
	text = startSpaces.ReplaceAllString(text, "\n")
	return strings.TrimSpace(blankLines.ReplaceAllString(text, "\n\n"))
}
func absolute(base, ref string) string {
	b, e := url.Parse(base)
	if e != nil {
		return ""
	}
	r, e := url.Parse(ref)
	if e != nil {
		return ""
	}
	return b.ResolveReference(r).String()
}
func ParseList(source, base string) ([]Notice, error) {
	root, e := html.Parse(strings.NewReader(source))
	if e != nil {
		return nil, e
	}
	container := first(root, func(n *html.Node) bool { return strings.HasPrefix(attr(n, "id"), "wp_news_w") })
	if container == nil {
		container = first(root, func(n *html.Node) bool { return strings.Contains(strings.ToLower(attr(n, "class")), "news") })
	}
	out := []Notice{}
	if container == nil {
		return out, nil
	}
	for _, row := range nodes(container, func(n *html.Node) bool { return tag(n, "tr") }) {
		main := first(row, func(n *html.Node) bool { return tag(n, "td") && hasClass(n, "main") })
		if main == nil {
			continue
		}
		outer := first(main, func(n *html.Node) bool { return tag(n, "a") })
		// 学校模板含非法嵌套 a；HTML5 解析会拆成相邻链接。
		// 选择仍携带标题/正文的链接，而不是被解析为空的外层 a。
		link := first(main, func(n *html.Node) bool { return tag(n, "a") && (attr(n, "title") != "" || inlineText(n) != "") })
		if link == nil {
			continue
		}
		title := attr(link, "title")
		if outer != nil && outer != link && attr(outer, "href") == attr(link, "href") {
			title = inlineText(link)
		}
		if title == "" {
			title = inlineText(link)
		}
		relative := attr(link, "href")
		parts := strings.Split(relative, "/")
		id := relative
		if len(parts) > 2 {
			id = parts[len(parts)-2]
		}
		if len(id) <= 5 || title == "" {
			continue
		}
		cells := nodes(row, func(n *html.Node) bool { return tag(n, "td") })
		date := "未知"
		if len(cells) > 1 {
			date = inlineText(cells[len(cells)-1])
		}
		out = append(out, Notice{SiteID: "jwc", ID: id, Title: title, URL: absolute(base, relative), Date: date})
	}
	return out, nil
}
func parseBody(source string) (*html.Node, error) {
	root, e := html.Parse(strings.NewReader(source))
	if e != nil {
		return nil, e
	}
	remove := nodes(root, func(n *html.Node) bool {
		if n.Type != html.ElementNode {
			return false
		}
		switch n.Data {
		case "script", "style", "meta", "link", "nav", "footer", "header", "aside", "noscript":
			return true
		}
		class := strings.ToLower(attr(n, "class"))
		for _, v := range []string{"wp_nav", "fontmain2", "nav-item", "copyright", "footer", "sidebar", "side"} {
			if strings.Contains(class, v) {
				return true
			}
		}
		return false
	})
	for _, n := range remove {
		if n.Parent != nil {
			n.Parent.RemoveChild(n)
		}
	}
	content := first(root, func(n *html.Node) bool { return hasClass(n, "wp_articlecontent") })
	for _, s := range []string{"content", "article", "main", "text", "bodytext", "vsbcontent", "con_content", "article-content", "articlecontent", "news-content", "newscontent"} {
		if content != nil {
			break
		}
		content = first(root, func(n *html.Node) bool {
			return strings.Contains(strings.ToLower(attr(n, "class")), s) || strings.Contains(strings.ToLower(attr(n, "id")), s)
		})
	}
	if content == nil {
		title := first(root, func(n *html.Node) bool { return tag(n, "span") && hasClass(n, "Article_Title") })
		if title != nil {
			content = title.Parent
			for p := title.Parent; p != nil; p = p.Parent {
				if tag(p, "td") {
					content = p
					break
				}
			}
		}
	}
	if content == nil {
		content = first(root, func(n *html.Node) bool { return tag(n, "body") })
	}
	if content == nil {
		content = root
	}
	return content, nil
}
func clone(n *html.Node) *html.Node {
	v := &html.Node{Type: n.Type, Data: n.Data, DataAtom: n.DataAtom, Attr: append([]html.Attribute(nil), n.Attr...)}
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		v.AppendChild(clone(c))
	}
	return v
}
func ParseDetail(source, base string) (Detail, error) {
	content, e := parseBody(source)
	if e != nil {
		return Detail{}, e
	}
	var b bytes.Buffer
	html.Render(&b, content)
	d := Detail{Text: Text(content), HTML: b.String(), RawHTML: source, Attachments: []Candidate{}}
	add := func(c Candidate) {
		if !IsCandidate(c) {
			return
		}
		for i, a := range d.Attachments {
			if a.URL == c.URL {
				d.Attachments[i] = c
				return
			}
		}
		d.Attachments = append(d.Attachments, c)
	}
	for _, link := range nodes(content, func(n *html.Node) bool { return tag(n, "a") && attr(n, "href") != "" }) {
		href := attr(link, "href")
		text := inlineText(link)
		if text == "" {
			text = href
		}
		add(Candidate{absolute(base, href), text, "detail_link"})
	}
	for _, p := range nodes(content, func(n *html.Node) bool { return hasClass(n, "wp_pdf_player") }) {
		file := attr(p, "pdfsrc")
		if file == "" {
			file = attr(p, "file")
		}
		origin := base
		if file == "" && attr(p, "src") != "" {
			origin = absolute(base, attr(p, "src"))
			u, e := url.Parse(origin)
			if e == nil {
				file = u.Query().Get("file")
			}
		}
		if file != "" {
			add(Candidate{absolute(origin, file), "附件.pdf", "pdf_player"})
		}
	}
	md := clone(content)
	for _, img := range nodes(md, func(n *html.Node) bool { return tag(n, "img") }) {
		src := attr(img, "data-src")
		if src == "" {
			src = attr(img, "src")
		}
		u := absolute(base, src)
		alt := attr(img, "alt")
		if alt == "" {
			alt = "正文图片"
		}
		c := Candidate{u, alt, "inline_image"}
		if src == "" || !IsCandidate(c) {
			img.Parent.RemoveChild(img)
			continue
		}
		add(c)
		alt = strings.NewReplacer("\n", " ", "\\", "\\\\", "[", "\\[", "]", "\\]").Replace(alt)
		p := &html.Node{Type: html.ElementNode, Data: "div"}
		p.AppendChild(&html.Node{Type: html.TextNode, Data: "![" + alt + "](<" + u + ">)"})
		img.Parent.InsertBefore(p, img)
		img.Parent.RemoveChild(img)
	}
	d.Markdown = Text(md)
	return d, nil
}
func fileSuffix(value string) string {
	u, e := url.Parse(value)
	if e != nil {
		return ""
	}
	return strings.ToLower(path.Ext(u.Path))
}

var imageExtensions = map[string]bool{".png": true, ".jpg": true, ".jpeg": true, ".gif": true, ".webp": true, ".svg": true, ".bmp": true}

func imageFile(name, ct string) bool {
	return strings.HasPrefix(strings.ToLower(ct), "image/") || imageExtensions[fileSuffix(name)]
}
func IsCandidate(c Candidate) bool {
	u, e := url.Parse(c.URL)
	if e != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return false
	}
	// UEditor 从 Word 粘贴图片时留下的占位资源，不是通知附件。
	resourcePath := strings.ToLower(u.Path)
	if strings.HasSuffix(resourcePath, "/ueditor/themes/default/images/spacer.gif") ||
		(strings.Contains(resourcePath, "/ueditor/lang/") && strings.HasSuffix(resourcePath, "/images/localimage.png")) {
		return false
	}
	if c.Source == "inline_image" || c.Source == "pdf_player" {
		return true
	}
	ext := fileSuffix(c.URL)
	if imageExtensions[ext] {
		return true
	}
	for _, v := range strings.Fields(".pdf .doc .docx .xls .xlsx .ppt .pptx .zip .rar .7z .txt") {
		if ext == v {
			return true
		}
	}
	if ext == ".htm" || ext == ".html" || strings.HasSuffix(u.Path, "/") {
		return false
	}
	text := strings.ToLower(strings.TrimSpace(c.Text))
	if text == "下载专区" || text == "下载中心" || text == "附件下载专区" {
		return false
	}
	for _, word := range strings.Fields("附件 下载 pdf doc xls ppt zip") {
		if strings.Contains(text, word) {
			return true
		}
	}
	return false
}
func Snippet(s string) string {
	s = spaces.ReplaceAllString(strings.TrimSpace(s), " ")
	r := []rune(s)
	if len(r) > 100 {
		return string(r[:100]) + "..."
	}
	return s
}
