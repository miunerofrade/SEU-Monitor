import requests
import json
import os
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timedelta

# === 1. 配置区 ===
# 从环境变量读取，安全第一
WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")
BASE_URL = "https://jwc.seu.edu.cn/"
# 这里的路径改为相对路径，GitHub 才能找到
STORE_ROOT = "store" 

COLUMNS = {
    "最新动态": "zxdt/list.htm",
    "教务信息": "jwxx/list.htm",
    "学籍管理": "xjgl/list.htm",
    "实践教学": "sjjx/list.htm",
    "国际交流": "gjjl/list.htm",
    "文化素质教育": "cbxx/list.htm"
}

# === 2. 功能函数 ===

def send_feishu_msg(column_name, title, date_text, notice_url):
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"🔔 [{column_name}] {title}",
                    "content": [
                        [{"tag": "text", "text": f"发布时间: {date_text}"}],
                        [{"tag": "a", "text": "🔗 点击查看原文", "href": notice_url}]
                    ]
                }
            }
        }
    }
    try:
        res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return res.json().get("code") == 0
    except:
        return False

def get_column_notices(full_url):
    notices = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(full_url, headers=headers, timeout=15)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        container = soup.find(id=lambda x: x and x.startswith('wp_news_w'))
        if not container: return notices

        rows = container.find_all('tr')
        for row in rows:
            main_td = row.find('td', class_='main')
            if not main_td: continue
            
            link_tag = main_td.find('a')
            if not link_tag: continue

            title = link_tag.get('title') or link_tag.get_text().strip()
            relative_link = link_tag.get('href')
            full_link = urljoin(full_url, relative_link)
            
            date_tds = row.find_all('td')
            date_text = date_tds[-1].get_text().strip() if len(date_tds) > 1 else "未知"

            try:
                parts = relative_link.split('/')
                n_id = parts[-2] if len(parts) > 2 else relative_link
            except:
                n_id = relative_link
            
            # 这里的 ID 防火墙必须保留！
            if n_id and len(n_id) > 5:
                notices.append({"id": n_id, "title": title, "link": full_link, "date": date_text})
    except Exception as e:
        print(f"抓取失败 {full_url}: {e}")
    return notices

# === 3. 运行逻辑 ===

def run_task():
    # 修正时区显示
    beijing_time = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    print(f"🚀 北京时间 {beijing_time} 开始扫描...")
    
    for col_name, path in COLUMNS.items():
        col_path = os.path.join(STORE_ROOT, col_name)
        if not os.path.exists(col_path):
            os.makedirs(col_path)
        log_file = os.path.join(col_path, "sent_ids.txt")

        sent_ids = set()
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                # 只读取有效的长 ID
                sent_ids = {line.strip() for line in f if len(line.strip()) > 5}

        current_url = urljoin(BASE_URL, path)
        all_notices = get_column_notices(current_url)
        
        count = 0
        for notice in reversed(all_notices):
            if notice["id"] not in sent_ids:
                print(f"✨ [{col_name}] 新消息: {notice['title']}")
                if send_feishu_msg(col_name, notice['title'], notice['date'], notice['link']):
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(notice["id"] + "\n")
                    sent_ids.add(notice["id"])
                    count += 1
                    time.sleep(2) 
        
        print(f"✅ {col_name} 处理完毕，新增 {count} 条。")

if __name__ == "__main__":

    run_task()

