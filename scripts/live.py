import requests, re, concurrent.futures as cf

# ============ 配置区 ============

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://gh-proxy.com/https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
]

OUT = "live.m3u"
TIMEOUT = 8
EPG = "https://epg.pw/xmltv/epg_CN.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://tv.cctv.com/",
}

# 组名 -> 匹配关键词（去横杠、去空格后比对）
GROUPS = {
    "央视频道": ["CCTV"],
    "卫视频道": ["卫视", "星空卫视", "翡翠台", "明珠台", "凤凰"],
    "地方台":   ["北京", "上海", "天津", "重庆", "广东", "广州", "深圳", "浙江", "江苏",
                 "南京", "苏州", "湖南", "湖北", "武汉", "山东", "青岛", "河南", "郑州",
                 "河北", "石家庄", "四川", "成都", "安徽", "合肥", "福建", "福州", "厦门",
                 "江西", "南昌", "陕西", "西安", "山西", "太原", "辽宁", "沈阳", "大连",
                 "吉林", "长春", "黑龙江", "哈尔滨", "云南", "昆明", "贵州", "贵阳",
                 "甘肃", "兰州", "新疆", "乌鲁木齐", "内蒙古", "广西", "南宁", "海南",
                 "宁夏", "青海", "西藏", "香港", "澳门", "台湾"],
}

# ============ 函数区 ============

def fetch(url):
    try:
        return requests.get(url, timeout=12, headers=HEADERS).text
    except Exception:
        return ""

def parse(text):
    items, name = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            name = m.group(1).strip() if m else "未知"
        elif line and not line.startswith("#") and name:
            items.append((name, line))
            name = None
    return items

def alive(url):
    """拉 m3u8 首段，200 且含 #EXTM3U 与至少一个分片/子列表才判活"""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
        if r.status_code != 200:
            return False
        head = r.text[:1024].lower()
        if "403 forbidden" in head or "not found" in head or "拒绝访问" in head:
            return False
        if "#extm3u" not in head:
            return False
        if "#ext-x-stream-inf" not in head and ".ts" not in head and ".m3u8" not in head:
            return False
        return True
    except Exception:
        return False

def clean_name(name):
    """显示名去掉 HD/FHD/UHD/高清/超清/括号分辨率"""
    n = re.sub(r"[（(]\s*\d+[pPiI]+\s*[)）]", "", name)
    n = re.sub(r"\s*(F?U?HD|高清|超清)\s*", "", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    return n

def norm(name):
    """归一化：去空格、去括号、去画质后缀、去横杠，用于去重"""
    n = clean_name(name)
    n = re.sub(r"\s+", "", n)
    return n.upper()

def match_group(name):
    """根据频道名判断分组"""
    n = name.upper().replace("-", "").replace(" ", "")
    for grp, kws in GROUPS.items():
        for kw in kws:
            if kw.upper().replace(" ", "") in n:
                return grp
    return "其他频道"

def sort_key(name):
    """央视按数字序；卫视、地方、其他依次在后"""
    m = re.search(r"CCTV[- ]?(\d+)", name, re.I)
    if m:
        return (0, int(m.group(1)), name)
    if "卫视" in name:
        return (1, 0, name)
    return (2, 0, name)

# ============ 主流程 ============

def main():
    pool = {}          # norm(name) -> (name, url)
    for u in UPSTREAMS:
        text = fetch(u)
        if not text:
            print(f"[跳过] 拉取失败: {u}")
            continue
        for name, url in parse(text):
            key = norm(name)
            if key in pool:
                continue
            pool[key] = (name, url)

    print(f"候选 {len(pool)} 个，开始测活...")

    good = []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(alive, v[1]): k for k, v in pool.items()}
        for f in cf.as_completed(futs):
            if f.result():
                good.append(pool[futs[f]])

    # 按央视->卫视->其他 排序，央视内部按数字
    good.sort(key=lambda x: sort_key(x[0]))

    # 统计每组数量
    stat = {}
    for name, url in good:
        g = match_group(name)
        stat[g] = stat.get(g, 0) + 1
    print("分组统计:", stat)

    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(f'#EXTM3U x-tvg-url="{EPG}"\n')
        for name, url in good:
            disp = clean_name(name)
            grp = match_group(disp)
            fp.write(f'#EXTINF:-1 group-title="{grp}",{disp}\n{url}\n')

    print(f"存活 {len(good)} 个频道，已写入 {OUT}")

if __name__ == "__main__":
    main()
