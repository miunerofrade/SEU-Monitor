package monitor

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
)

func postNotification(ctx context.Context, webhook, title string, lines [][]map[string]string) error {
	if webhook == "" {
		return errors.New("飞书地址未配置")
	}
	body, _ := json.Marshal(map[string]any{"msg_type": "post", "content": map[string]any{"post": map[string]any{"zh_cn": map[string]any{"title": title, "content": lines}}}})
	client := NewClient("")
	defer client.CloseIdleConnections()
	r, e := request(ctx, client, "POST", webhook, bytes.NewReader(body))
	if e != nil {
		return errors.New("飞书推送请求失败")
	}
	defer r.Body.Close()
	var value struct {
		Code *int `json:"code"`
	}
	if r.StatusCode >= 400 || json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&value) != nil || value.Code == nil || *value.Code != 0 {
		return errors.New("飞书推送未成功，下次重试")
	}
	return nil
}
func SendNotice(ctx context.Context, webhook string, n Notice, text string) error {
	lines := [][]map[string]string{{{"tag": "text", "text": "发布时间: " + n.Date}}}
	if text != "" {
		lines = append(lines, []map[string]string{{"tag": "text", "text": Snippet(text)}})
	}
	lines = append(lines, []map[string]string{{"tag": "a", "text": "🔗 点击查看原文", "href": n.URL}})
	return postNotification(ctx, webhook, "🔔 ["+columnName(n.ColumnID)+"] "+n.Title, lines)
}
func alert(ctx context.Context, c Config) error {
	return postNotification(ctx, c.Webhook, "SEU-Monitor：VPN 已暂停", [][]map[string]string{{{"tag": "text", "text": "VPN 连接失败或需要人工验证，已暂停 VPN 自动重试。通知监控继续通过直连抓取。查看 VPN 日志后执行 monitor vpn 恢复；需要短信验证时执行 monitor vpn --interactive。"}}})
}
func Scan(ctx context.Context, root string, config Config, dry bool) (int, error) {
	release, e := scanLock(root)
	if e != nil {
		return 0, e
	}
	defer release()
	proxy := ""
	if config.Account != "" {
		proxy = fmt.Sprintf("http://127.0.0.1:%d", config.Port)
		if !Probe(ctx, proxy) {
			log.Print("校园 VPN 不可用，本轮改为直连抓取")
			proxy = ""
		}
	}
	client := NewClient(proxy)
	defer client.CloseIdleConnections()
	index := loadIndex(root)
	total := 0
	for _, column := range Columns {
		if ctx.Err() != nil {
			return total, ctx.Err()
		}
		seen := seenIDs(root, column.Name)
		html, e := page(ctx, client, column.URL)
		if e != nil {
			log.Printf("%s 列表抓取失败，下次重试：%v", column.Name, e)
			continue
		}
		notices, e := ParseList(html, column.URL)
		if e != nil {
			continue
		}
		for i := len(notices) - 1; i >= 0; i-- {
			n := notices[i]
			n.ColumnID = column.ID
			if seen[n.ID] {
				continue
			}
			if e = processNotice(ctx, root, config, client, index, seen, n, dry); e != nil {
				log.Printf("%s 处理失败，下次重试：%v", n.Title, e)
			} else if !dry {
				total++
			}
		}
	}
	log.Printf("扫描完成，成功处理 %d 条通知", total)
	return total, nil
}

var errDuplicate = errors.New("重复内容，已跳过")

func processNotice(ctx context.Context, root string, config Config, client *http.Client, index contentIndex, seen map[string]bool, n Notice, dry bool) error {
	source, e := page(ctx, client, n.URL)
	if e != nil {
		return e
	}
	d, e := ParseDetail(source, n.URL)
	if e != nil {
		return e
	}
	if dry {
		log.Printf("试运行：[%s] %s", columnName(n.ColumnID), n.Title)
		return nil
	}
	dir := directory(root, n)
	attachments := DownloadAttachments(ctx, client, d.Attachments, filepath.Join(dir, "attachments"), reusable(dir))
	m, e := SaveSnapshot(dir, n, d, attachments)
	if e != nil {
		return errors.New("归档保存失败")
	}
	for _, a := range attachments {
		if a.Error != "" {
			return fmt.Errorf("附件失败：%s", a.Error)
		}
	}
	if duplicate := index.find(m, d.Text); duplicate != "" && duplicate != dir {
		if e = markSeen(root, columnName(n.ColumnID), n.ID); e != nil {
			return e
		}
		seen[n.ID] = true
		if e = os.RemoveAll(dir); e != nil {
			return e
		}
		log.Printf("跳过重复内容：%s", n.Title)
		return errDuplicate
	}
	if config.Webhook != "" {
		if e = SendNotice(ctx, config.Webhook, n, d.Text); e != nil {
			return e
		}
	}
	if e = markSeen(root, columnName(n.ColumnID), n.ID); e != nil {
		return e
	}
	seen[n.ID] = true
	index.add(m, dir, d.Text)
	return nil
}
