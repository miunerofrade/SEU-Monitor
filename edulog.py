import requests
import json
import os
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# === 1. 配置区 ===
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/87a8364e-1db2-4b12-8fbe-309ef88394ce"
BASE_URL = "https://jwc.seu.edu.cn/"
STORE_ROOT = "store"

# 定义需要监控的栏目映射：{ "文件夹名": "网页路径" }
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
    """发送分类富文本消息"""
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    # 标题带上所属栏目，方便一眼识别
                    "title": f"🔔 [{column_name}] 新通知: {title}",
                    "content": [
                        [{"tag": "text", "text": f"发布时间: {date_text}"}],
                        [{"tag": "a", "text": "🔗 点击查看原文公告", "href": notice_url}]
                    ]
                }
            }
        }
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return True
    except:
        return False

def get_column_notices(column_url):
    """通用抓取函数"""
    notices = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(column_url, headers=headers, timeout=15)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 定位 ID（SEU 不同栏目的容器 ID 可能递增，如 w8, w9，但通常都在这个区域）
        # 这里使用更通用的 find 方式
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
            full_link = urljoin(column_url, relative_link)
            
            date_tds = row.find_all('td')
            date_text = date_tds[-1].get_text().strip() if len(date_tds) > 1 else "查看详情"

            # 提取唯一 ID
            try:
                n_id = relative_link.split('/')[-2]
            except:
                n_id = relative_link
            
            if len(n_id) > 5: # 长度防火墙
                notices.append({"id": n_id, "title": title, "link": full_link, "date": date_text})
    except Exception as e:
        print(f"抓取失败: {column_url}, 错误: {e}")
    return notices

# === 3. 主程序逻辑 ===

def run_multi_column():
    print(f"🚀 开始全栏目扫描任务...")
    
    for col_name, path in COLUMNS.items():
        print(f"\n--- 正在检查栏目: {col_name} ---")
        
        # 1. 动态准备文件夹和日志文件
        col_dir = os.path.join(STORE_ROOT, col_name)
        if not os.path.exists(col_dir):
            os.makedirs(col_dir)
        log_file = os.path.join(col_dir, "sent_ids.txt")

        # 2. 读取该栏目的历史记录
        sent_ids = set()
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                sent_ids = {line.strip() for line in f if len(line.strip()) > 5}

        # 3. 抓取并推送
        current_url = urljoin(BASE_URL, path)
        notices = get_column_notices(current_url)
        
        new_count = 0
        for notice in reversed(notices):
            if notice["id"] not in sent_ids:
                print(f"✨ [{col_name}] 发现新通知: {notice['title']}")
                if send_feishu_msg(col_name, notice['title'], notice['date'], notice['link']):
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(notice["id"] + "\n")
                    new_count += 1
                    time.sleep(1) # 栏目内间隔
            else:
                pass 
        
        print(f"✅ {col_name} 检查完毕，推送 {new_count} 条。")
        time.sleep(2) # 栏目间切换间隔

if __name__ == "__main__":
    run_multi_column()