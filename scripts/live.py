import requests, re, concurrent.futures as cf

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    # 国内加速镜像，主源拉不到时兜底
    "https://gh-proxy.com/https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
]

OUT = "live.m3u"
TIMEOUT = 8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://tv.cctv.com/",
}

# 只保留这些关键词的台，留空 = 不过滤
WHITELIST = [
    "CCTV-1", "CCTV-2", "CCTV-3", "CCTV-4", "CCTV-5", "CCTV-6",
    "CCTV-7", "CCTV-8", "CCTV-9", "CCTV-10", "CCTV-11", "CCTV-12",
    "CCTV-13", "CCTV-14", "CCTV-15",
    "湖南卫视", "浙江卫视", "江苏卫视", "东方卫视", "北京卫视",
    "广东卫视", "山东卫视", "安徽卫视", "湖北卫视", "四川卫视",
]

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
    """真拉 m3u8，必须含 #EXTM3U 且至少有一个分片/子列表，403/空页/鉴权页一律判死"""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, stream=True)
        if r.status_code != 200:
            r.close()
            return False
        head = r.raw.read(4096).decode("utf-8", "ignore")
        r.close()
        low = head.lower()
        if "拒绝访问" in head or "403 forbidden" in low or "not found" in low:
            return False
        if "#EXTM3U" not in head:
            return False
        # 主列表或子列表至少有一个真实地址
        if "#EXT-X-STREAM-INF" not in head and ".ts" not in low and ".m3u8" not in low:
            return False
        return True
    except Exception:
        return False

def norm(name):
    """频道名归一化，用于去重"""
    n = re.sub(r"\s+", "", name)
    n = re.sub(r"[（(].*?[)）]", "", n)   # 去括号后缀
    n = n.replace("高清", "").replace("HD", "").replace("超清", "")
    return n.upper()

def main():
    seen, pool = set(), {}
    for u in UPSTREAMS:
        text = fetch(u)
        if not text:
            print(f"[跳过] 拉取失败: {u}")
            continue
        for name, url in parse(text):
            if WHITELIST and not any(w in name for w in WHITELIST):
                continue
            key = norm(name)
            if key in seen:
                continue
            seen.add(key)
            pool[key] = (name, url)

    print(f"候选 {len(pool)} 个，开始测活...")

    good = []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(alive, v[1]): k for k, v in pool.items()}
        for f in cf.as_completed(futs):
            if f.result():
                good.append(pool[futs[f]])
    good.sort(key=lambda x: x[0])

    def logo_for(name):
        m = {
            "CCTV-1": "https://epg.pw/logo/cctv1.png",
            "CCTV-5": "https://epg.pw/logo/cctv5.png",
            "CCTV-6": "https://epg.pw/logo/cctv6.png",
            "CCTV-13": "https://epg.pw/logo/cctv13.png",
            "湖南卫视": "https://epg.pw/logo/hunanws.png",
            "浙江卫视": "https://epg.pw/logo/zhejiangws.png",
            "江苏卫视": "https://epg.pw/logo/jiangsuws.png",
        }
        for k, v in m.items():
            if k in name:
                return v
        return ""

    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write('#EXTM3U x-tvg-url="https://epg.pw/xmltv/epg_CN.xml"\n')
        for name, url in good:
            logo = logo_for(name)
            grp = "央视频道" if "CCTV" in name else "卫视频道"
            attr = f'tvg-logo="{logo}" ' if logo else ""
            fp.write(f'#EXTINF:-1 {attr}group-title="{grp}",{name}\n{url}\n')

    print(f"存活 {len(good)} 个频道")

if __name__ == "__main__":
    main()
