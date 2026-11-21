import requests, re, concurrent.futures as cf

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
]
OUT = "live.m3u"
TIMEOUT = 6
HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch(url):
    try:
        return requests.get(url, timeout=10, headers=HEADERS).text
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
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, stream=True)
        ok = r.status_code == 200 and (b"#EXTM3U" in r.raw.read(200) or r.status_code == 200)
        r.close()
        return ok
    except Exception:
        return False

def main():
    seen, pool = set(), {}
    for u in UPSTREAMS:
        for name, url in parse(fetch(u)):
            key = re.sub(r"\s+", "", name)
            if key in seen:
                continue
            seen.add(key)
            pool[key] = (name, url)

    good = []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(alive, v[1]): k for k, v in pool.items()}
        for f in cf.as_completed(futs):
            if f.result():
                good.append(pool[futs[f]])
    good.sort(key=lambda x: x[0])

    def logo_for(name):
        m = {
            "CCTV-6": "https://epg.pw/logo/cctv6.png",
            "CCTV-13": "https://epg.pw/logo/cctv13.png",
            "湖南卫视": "https://epg.pw/logo/hunanws.png",
            "浙江卫视": "https://epg.pw/logo/zhejiangws.png",
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
