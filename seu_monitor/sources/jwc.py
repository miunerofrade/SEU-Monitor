BASE_URL = "https://jwc.seu.edu.cn"
COLUMNS = {
    "zxdt": "最新动态",
    "jwxx": "教务信息",
    "xjgl": "学籍管理",
    "sjjx": "实践教学",
    "gjjl": "国际交流",
    "cbxx": "文化素质教育",
}


def site_config():
    return {
        "id": "jwc",
        "name": "教务处",
        "base_url": BASE_URL,
        "columns": [
            {"id": key, "name": name, "list_url": f"{BASE_URL}/{key}/list.htm"}
            for key, name in COLUMNS.items()
        ],
    }
