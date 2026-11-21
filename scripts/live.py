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

    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write('#EXTM3U x-tvg-url="https://epg.pw/xmltv/epg_CN.xml"\n')
        for name, url in good:
            fp.write(f'#EXTINF:-1 group-title="直播",{name}\n{url}\n')
    print(f"存活 {len(good)} 个频道")

if __name__ == "__main__":
    main()