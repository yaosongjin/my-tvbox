import re
import signal
import requests
import concurrent.futures as cf

OUT = "live.m3u"
EPG = "https://epg.pw/xmltv/epg_CN.xml"

TIMEOUT = (4, 8)
HARD_LIMIT = 200
MAX_WORKERS = 16

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/iptv.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
]

# 央视核心台保底：测活失败也写入，保证 CCTV-4/5/6 一定在
CORE_CCTV = {
    "CCTV-4": ["http://112.74.115.68:35455/gaoma/cctv4k.m3u8"],
    "CCTV-5": ["http://138.2.110.209:35455/gaoma/cctv5.m3u8"],
    "CCTV-5+": ["http://138.2.110.209:35455/gaoma/cctv5p.m3u8"],
    "CCTV-6": ["https://stream1.freetv.fun/abb9/ott.m3u8",
               "http://69.30.245.50/live/cctv6.m3u8"],
}

KEEP = [
    "CCTV", "卫视",
    "Hunan", "Zhejiang", "Jiangsu", "Oriental", "Beijing", "Guangdong",
    "Shandong", "Anhui", "Hubei", "Sichuan", "Fujian", "Jiangxi",
    "Henan", "Hebei", "Shaanxi", "Shanxi", "Liaoning", "Jilin",
    "Heilongjiang", "Yunnan", "Guizhou", "Gansu", "Xinjiang",
    "Inner Mongolia", "Guangxi", "Hainan", "Ningxia", "Qinghai", "Tibet",
    "翡翠", "明珠", "凤凰",
    "北京", "上海", "天津", "重庆", "广东", "广州", "深圳",
    "浙江", "江苏", "南京", "苏州", "湖南", "湖北", "武汉",
    "山东", "青岛", "河南", "郑州", "河北", "成都", "合肥",
    "福州", "厦门", "南昌", "西安", "太原", "沈阳", "大连",
    "长春", "哈尔滨", "昆明", "贵阳", "兰州", "南宁",
    "香港", "澳门", "台湾",
]

SATELLITE_WHITELIST = {
    "HUNAN", "ZHEJIANG", "JIANGSU", "ORIENTAL", "BEIJING", "TIANJIN",
    "CHONGQING", "SHANDONG", "ANHUI", "HUBEI", "SICHUAN", "GUANGDONG",
    "SHENZHEN", "FUJIAN", "JIANGXI", "HENAN", "HEBEI", "SHAANXI",
    "SHANXI", "LIAONING", "JILIN", "HEILONGJIANG", "YUNNAN", "GUIZHOU",
    "GANSU", "QINGHAI", "HAINAN", "NINGXIA", "XINJIANG", "TIBET",
    "INNERMONGOLIA", "GUANGXI", "SHANGHAI",
}
HKMO_SAT = {"PHOENIX", "FEICUI", "MINGZHU", "STAR", "TVB", "CTI"}
LOCAL_KEYS = [
    "经济", "教育", "综艺", "国际", "藏语", "Economy", "Education",
    "Variety", "International", "Tibetan",
    "北京", "上海", "天津", "重庆", "广东", "广州", "深圳",
    "浙江", "江苏", "南京", "苏州", "湖南", "湖北", "武汉",
    "山东", "青岛", "河南", "郑州", "河北", "成都", "合肥",
    "福州", "厦门", "南昌", "西安", "太原", "沈阳", "大连",
    "长春", "哈尔滨", "昆明", "贵阳", "兰州", "南宁",
    "香港", "澳门", "台湾",
]


def norm(name):
    return name.upper().replace(" ", "").replace("-", "").replace("_", "")


def clean_name(name):
    n = re.sub(r"\[.*?\]", "", name)
    n = re.sub(r"[（(]\s*\d+[pPiI]+\s*[)）]", "", n)
    n = re.sub(r"\s*(F?U?HD|高清|超清)\s*", "", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def fetch(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[拉取失败] {url} -> {e}")
        return ""


def parse(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        name = line.split(",", 1)[-1].strip()
        for j in range(i + 1, min(i + 6, len(lines))):
            if lines[j].startswith("http"):
                yield name, lines[j]
                break


def match_group(name):
    n = norm(name)
    if "CCTV" in n or "央视" in name:
        return "央视频道"
    if any(k in n for k in HKMO_SAT):
        return "卫视频道"
    if "卫视" in name:
        return "卫视频道"
    if re.match(r"^[A-Z]+TV$", name.strip(), re.I) and \
       any(s in n for s in SATELLITE_WHITELIST):
        return "卫视频道"
    if re.search(r"[A-Z]+SATELLITE\s*TV", n) and \
       any(s in n for s in SATELLITE_WHITELIST):
        return "卫视频道"
    for kw in LOCAL_KEYS:
        if norm(kw) in n:
            return "地方台"
    return "其他频道"


def sort_key(name):
    m = re.search(r"CCTV[- ]?(\d+)", name, re.I)
    if m:
        return (0, int(m.group(1)), name)
    g = match_group(name)
    if g == "卫视频道":
        return (1, 0, name)
    if g == "地方台":
        return (2, 0, name)
    return (3, 0, name)


def alive(url):
    """只判 HTTP 200 + 不是明显错误页，放宽对 m3u8 内容的要求"""
    if not url.startswith("http"):
        return False
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS,
                         allow_redirects=True, stream=True)
        if r.status_code != 200:
            return False
        head = r.raw.read(1024, decode_content=True).decode("utf-8", "ignore").lower()
        r.close()
        for bad in ("you don't have authorization", "403 forbidden",
                    "not found", "拒绝访问", "无权限", "404"):
            if bad in head:
                return False
        if "#extm3u" in head:
            return True
        # 有些源首包是 ts 段，也认为活
        if head.startswith("\x47") or ".ts" in head:
            return True
        return False
    except Exception:
        return False


def _alarm(*_):
    raise TimeoutError("hard limit reached")


def main():
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(HARD_LIMIT)
    except Exception:
        pass

    raw_pool = {}
    for u in UPSTREAMS:
        text = fetch(u)
        if not text:
            continue
        for name, url in parse(text):
            key = norm(name)
            if key not in raw_pool:
                raw_pool[key] = (name, url)

    print(f"解析原始 {len(raw_pool)} 个")

    pool = {k: v for k, v in raw_pool.items()
            if any(norm(w) in k for w in KEEP)}
    print(f"粗筛后候选 {len(pool)} 个")
    print("粗筛样本:", [clean_name(v[0]) for v in list(pool.values())[:30]])

    good = {}
    try:
        with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(alive, v[1]): k for k, v in pool.items()}
            for f in cf.as_completed(futs):
                if f.result():
                    good[futs[f]] = pool[futs[f]]
    except TimeoutError:
        print("[硬超时] 停止测活，写入已存活频道")
    except Exception as e:
        print(f"[测活异常] {e}")

    # 央视核心台保底：测活失败也留，保证 4/5/6 在
    for cname, urls in CORE_CCTV.items():
        key = norm(cname)
        if key in good:
            continue
        for u in urls:
            if alive(u):
                good[key] = (cname, u)
                print(f"[央视保底] {cname} -> {u}")
                break
        else:
            good[key] = (cname, urls[0])
            print(f"[央视兜底-未测活] {cname} -> {urls[0]}")

    result = sorted(good.values(), key=lambda x: sort_key(x[0]))

    stat = {}
    for name, _ in result:
        g = match_group(name)
        stat[g] = stat.get(g, 0) + 1
    print("分组统计:", stat)

    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(f'#EXTM3U x-tvg-url="{EPG}"\n')
        for name, url in result:
            disp = clean_name(name)
            grp = match_group(disp)
            fp.write(f'#EXTINF:-1 group-title="{grp}",{disp}\n{url}\n')

    print(f"写入 {len(result)} 个频道到 {OUT}")
    print("最终频道:", [clean_name(n) for n, _ in result])


if __name__ == "__main__":
    main()
