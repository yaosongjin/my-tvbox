import requests, re, concurrent.futures as cf

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://gh-proxy.com/https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
]

OUT = "live.m3u"
TIMEOUT = 8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://tv.cctv.com/",
}

WHITELIST = [
    "CCTV-1", "CCTV-2", "CCTV-3", "CCTV-4", "CCTV-5", "CCTV-6",
    "CCTV-7", "CCTV-8", "CCTV-9", "CCTV-10", "CCTV-11", "CCTV-12",
    "CCTV-13", "CCTV-14", "CCTV-15",
    "湖南卫视", "浙江卫视", "江苏卫视", "东方卫视", "北京卫视",
    "广东卫视", "山东卫视", "安徽卫视", "湖北卫视", "四川卫视",
]

# CCTV6 兜底源：上游全挂时至少留一条，换成你实测能播的
CCTV6_FALLBACK = [
    "http://74.91.26.218:82/live/cctv6hd.m3u8",
    "http://69.38.245.59/live/cctv6.m3u8",
]

LOGO_MAP = {
    "CCTV-1": "https://epg.pw/logo/cctv1.png",
    "CCTV-2": "https://epg.pw/logo/cctv2.png",
    "CCTV-3": "https://epg.pw/logo/cctv3.png",
    "CCTV-4": "https://epg.pw/logo/cctv4.png",
    "CCTV-5": "https://epg.pw/logo/cctv5.png",
    "CCTV-6": "https://epg.pw/logo/cctv6.png",
    "CCTV-7": "https://epg.pw/logo/cctv7.png",
    "CCTV-8": "https://epg.pw/logo/cctv8.png",
    "CCTV-9": "https://epg.pw/logo/cctv9.png",
    "CCTV-10": "https://epg.pw/logo/cctv10.png",
    "CCTV-11": "https://epg.pw/logo/cctv11.png",
    "CCTV-12": "https://epg.pw/logo/cctv12.png",
    "CCTV-13": "https://epg.pw/logo/cctv13.png",
    "CCTV-14": "https://epg.pw/logo/cctv14.png",
    "CCTV-15": "https://epg.pw/logo/cctv15.png",
    "湖南卫视": "https://epg.pw/logo/hunanws.png",
    "浙江卫视": "https://epg.pw/logo/zhejiangws.png",
    "江苏卫视": "https://epg.pw/logo/jiangsuws.png",
    "东方卫视": "https://epg.pw/logo/dongfangws.png",
    "北京卫视": "https://epg.pw/logo/beijingws.png",
    "广东卫视": "https://epg.pw/logo/guangdongws.png",
    "山东卫视": "https://epg.pw/logo/shandongws.png",
    "安徽卫视": "https://epg.pw/logo/anhuiws.png",
    "湖北卫视": "https://epg.pw/logo/hubeiws.png",
    "四川卫视": "https://epg.pw/logo/sichuanws.png",
}


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
    """真拉 m3u8：200 且含 #EXTM3U，主列表或子列表至少有一个真实地址"""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
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


def norm(name):
    """频道名归一化，用于去重；CCTV-6 与 CCTV6 归一到同一 key"""
    n = re.sub(r"\s+", "", name)
    n = re.sub(r"[（(].*?[)）]", "", n)
    n = n.replace("高清", "").replace("超清", "").replace("HD", "")
    n = n.upper().replace("CCTV-", "CCTV")
    return n


def clean_name(name):
    """显示名去掉 HD/FHD/UHD/高清/超清 及括号分辨率后缀"""
    n = re.sub(r"[（(]\s*\d+[pP][^)）]*[)）]", "", name)
    n = re.sub(r"\s*(F?U?HD|高清|超清)\s*", "", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def match_whitelist(name):
    """去横杠比对，CCTV6 / CCTV-6 / CCTV-6 HD 都能命中"""
    n = name.upper().replace("-", "").replace(" ", "")
    for w in WHITELIST:
        if w.upper().replace("-", "").replace(" ", "") in n:
            return True
    return False


def sort_key(name):
    """CCTV 按数字自然序，卫视排后面"""
    m = re.search(r"CCTV[- ]?(\d+)", name, re.I)
    if m:
        return (0, int(m.group(1)), name)
    return (1, 0, name)


def main():
    seen, pool = set(), {}
    for u in UPSTREAMS:
        text = fetch(u)
        if not text:
            print(f"[跳过] 拉取失败: {u}")
            continue
        for name, url in parse(text):
            if WHITELIST and not match_whitelist(name):
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

    # CCTV6 兜底
    if not any("CCTV6" in norm(n) for n, _ in good):
        for fb in CCTV6_FALLBACK:
            if alive(fb):
                good.append(("CCTV-6", fb))
                print(f"[兜底] CCTV6 使用备用源: {fb}")
                break

    good.sort(key=lambda x: sort_key(x[0]))

    def logo_for(name):
        for k, v in LOGO_MAP.items():
            if k.replace("-", "") in name.upper().replace("-", ""):
                return v
        return ""

    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write('#EXTM3U x-tvg-url="https://epg.pw/xmltv/epg_CN.xml"\n')
        for name, url in good:
            disp = clean_name(name)
            logo = logo_for(disp)
            grp = "央视频道" if "CCTV" in disp.upper() else "卫视频道"
            attr = f'tvg-logo="{logo}" ' if logo else ""
            fp.write(f'#EXTINF:-1 {attr}group-title="{grp}",{disp}\n{url}\n')

    print(f"存活 {len(good)} 个频道")


if __name__ == "__main__":
    main()
