import json
import os
import re
import signal
import requests
import concurrent.futures as cf

OUT = "live.m3u"
EPG = "https://epg.pw/xmltv/epg_CN.xml"

TIMEOUT = (3, 5)
HARD_LIMIT = 240
MAX_WORKERS = 24

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://gh-proxy.com/https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
]

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
    "长春", "哈尔滨", "昆明", "贵阳", "兰州", "南宁", "香港", "澳门", "台湾",
]

GROUPS = {
    "央视频道": ["CCTV", "央视"],
    "卫视频道": ["卫视", "Hunan", "Zhejiang", "Jiangsu", "Oriental", "Beijing",
              "Guangdong", "Shandong", "Anhui", "Hubei", "Sichuan", "Fujian",
              "Jiangxi", "Henan", "Hebei", "Shaanxi", "Shanxi", "Liaoning",
              "Jilin", "Heilongjiang", "Yunnan", "Guizhou", "Gansu",
              "Xinjiang", "Guangxi", "Hainan", "凤凰", "翡翠", "明珠"],
    "地方台": ["北京", "上海", "天津", "重庆", "广东", "广州", "深圳",
             "浙江", "江苏", "南京", "苏州", "湖南", "湖北", "武汉",
             "山东", "青岛", "河南", "郑州", "河北", "成都", "合肥",
             "福州", "厦门", "南昌", "西安", "太原", "沈阳", "大连",
             "长春", "哈尔滨", "昆明", "贵阳", "兰州", "南宁",
             "香港", "澳门", "台湾"],
}


def norm(name):
    return name.upper().replace(" ", "").replace("-", "").replace("_", "")


def clean_name(name):
    name = re.sub(r"\[.*?\]", "", name).strip()
    return name


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
        if i + 1 < len(lines) and lines[i + 1].startswith("http"):
            yield name, lines[i + 1]


def match_group(name):
    n = norm(name)
    for grp, kws in GROUPS.items():
        for kw in kws:
            if norm(kw) in n:
                return grp
    return "其他频道"


def sort_key(name):
    m = re.search(r"CCTV[- ]?(\d+)", name, re.I)
    if m:
        return (0, int(m.group(1)), name)
    if "卫视" in name or any(norm(k) in norm(name) for k in GROUPS["卫视频道"]):
        return (1, 0, name)
    return (2, 0, name)


def alive(url):
    if not url.startswith("http"):
        return False
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, stream=True)
        ok = r.status_code == 200 and (
            "mpegurl" in r.headers.get("Content-Type", "").lower()
            or "#extm3u" in r.text[:200].lower()
        )
        r.close()
        return ok
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

    good = []
    try:
        with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(alive, v[1]): k for k, v in pool.items()}
            for f in cf.as_completed(futs):
                if f.result():
                    good.append(pool[futs[f]])
    except TimeoutError:
        print("[硬超时] 停止测活，写入已存活频道")
    except Exception as e:
        print(f"[测活异常] {e}")

    good.sort(key=lambda x: sort_key(x[0]))

    stat = {}
    for name, _ in good:
        g = match_group(name)
        stat[g] = stat.get(g, 0) + 1
    print("分组统计:", stat)

    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(f'#EXTM3U x-tvg-url="{EPG}"\n')
        for name, url in good:
            disp = clean_name(name)
            grp = match_group(disp)
