import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT = "live.m3u"
TIMEOUT = (5, 10)
MAX_WORKERS = 16
MAX_LINES = 3
HARD_LIMIT = 300

UA = {"User-Agent": "Mozilla/5.0"}
UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/iptv.m3u",
]

BAD = [b"400-788", b"consultation hotline", b"consulting hotline"]

def clean_name(s):
    s = re.sub(r"\s+", "", s or "")
    return s.strip()

def norm(s):
    return re.sub(r"[\s_\-（）()]+", "", clean_name(s)).lower()

def match_group(name):
    if re.match(r"^cctv\d", name, re.I):
        return "央视频道"
    if "卫视" in name:
        return "卫视频道"
    return "地方台"

def parse(text):
    out, name = [], ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            name = m.group(1) if m else ""
        elif line and not line.startswith("#"):
            out.append((name, line))
            name = ""
    return out

def fetch(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        if r.status_code == 200 and "#EXTM3U" in r.text[:500].upper():
            return r.text
    except Exception as e:
        print(f"  X 拉取失败 {url} {e}")
        return None
    print(f"  X 拉取失败 {url}")
    return None

def probe(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        if r.status_code != 200:
            return False
        text = r.text[:8192]
        low = text.lower()
        if b"#extm3u" not in low:
            return False
        if any(b in low for b in BAD):
            return False
        # 宽松：有分片、或 m3u 里有 #EXTINF/#EXT-X-STREAM-INF 就算入口合法
        if re.search(r"\.ts|\.m4s|\.mp4|#EXT-X-STREAM-INF|#EXTINF", low, re.I):
            return True
        return False
    except Exception:
        return False

def alive(url):
    # 只测一次，不再二次连测
    return probe(url)

def preferred_rank(url):
    u = url.lower()
    if "fanmingming" in u or "gaoma" in u:
        return 0
    if "iptv-org" in u or "cctv" in u:
        return 1
    if re.search(r"\d{1,3}(\.\d{1,3}){3}:\d+", u):
        return 3
    return 2

def main():
    raw = []
    for up in UPSTREAMS:
        txt = fetch(up)
        if txt:
            items = parse(txt)
            print(f"  √ 解析 {len(items)} 条 {up}")
            raw.extend(items)
    print(f"[原始] 共 {len(raw)} 条")

    seen = {}
    for name, url in raw:
        cn = clean_name(name)
        if not cn:
            continue
        k = norm(cn)
        seen.setdefault(k, {"cname": cn, "grp": match_group(cn), "urls": []})
        if url not in seen[k]["urls"]:
            seen[k]["urls"].append(url)
    cand = []
    for data in seen.values():
        for u in data["urls"][:5]:
            cand.append((data["cname"], u, data["grp"]))
    cand = cand[:HARD_LIMIT]
    print(f"[候选] {len(cand)} 条")

    ok = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fs = {ex.submit(alive, u): (n, u, g) for n, u, g in cand}
        for f in as_completed(fs):
            n, u, g = fs[f]
            try:
                if f.result():
                    ok.append((n, u, g))
            except Exception:
                pass
    print(f"[测活通过] {len(ok)} 条")

    by_name = {}
    for cname, url, grp in ok:
        key = norm(cname)
        by_name.setdefault(key, {"cname": cname, "grp": grp, "urls": []})
        if url not in by_name[key]["urls"]:
            by_name[key]["urls"].append(url)

    order = {"央视频道": 0, "卫视频道": 1, "地方台": 2}
    final = []
    for key, data in by_name.items():
        urls = sorted(data["urls"], key=preferred_rank)[:MAX_LINES]
        for u in urls:
            final.append((data["cname"], u, data["grp"]))
    final.sort(key=lambda x: (order.get(x[2], 9), norm(x[0])))

    stats = {}
    for _, _, g in final:
        stats[g] = stats.get(g, 0) + 1
    print(f"[分组统计] {stats}")

    lines = ["#EXTM3U"]
    last_base, line_no = None, 0
    for cname, url, grp in final:
        base = clean_name(cname)
        if base == last_base:
            line_no += 1
        else:
            line_no, last_base = 1, base
        disp = f"{base} 线路{line_no}"
        lines.append(f'#EXTINF:-1 tvg-name="{base}" group-title="{grp}",{disp}')
        lines.append(url)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[最终频道-含线路] {len(final)} 条")
    print(f"[去重后台数] {len(by_name)} 个")
    for cname, url, grp in final:
        print(f"  - [{grp}] {cname}  {url}")

if __name__ == "__main__":
    main()
