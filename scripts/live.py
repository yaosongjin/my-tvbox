import os
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT = "live.m3u"
BACKUP = "live.m3u.bak"
UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/iptv.m3u",
]
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 4
MAX_WORKERS = 10
MAX_LINES = 3
HARD_LIMIT = 600

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"  拉取失败 {url}: {e}")
    return None

def parse(text):
    items = []
    name = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            m = re.search(r",(.*)$", line)
            name = m.group(1).strip() if m else None
        elif line and not line.startswith("#") and name:
            items.append((name, line))
            name = None
    return items

def clean_name(s):
    s = re.sub(r"\s+线路\d+$", "", s).strip()
    s = re.sub(r"[（(].*?[)）]", "", s).strip()
    return s

def norm(s):
    return re.sub(r"[\s\-_]", "", s).lower()

def match_group(name):
    if "cctv" in name.lower():
        return "央视频道"
    if any(x in name for x in ["卫视", "湖南", "浙江", "江苏", "东方", "广东"]):
        return "卫视频道"
    return "地方台"

def bad_source(url):
    u = url.lower()
    return any(x in u for x in ["400-", "咨询热线", "jump", "ad.", "landing"])

def probe(url):
    if bad_source(url):
        return False
    try:
        r = requests.get(url, headers={**HEADERS, "Range": "bytes=0-1023"}, timeout=TIMEOUT, stream=True)
        if r.status_code not in (200, 206):
            return False
        head = ""
        for chunk in r.iter_content(1024):
            head += chunk.decode("utf-8", "ignore")
            break
        r.close()
        return "#extm3u" in head.lower() or ".ts" in head.lower() or head.startswith("#")
    except Exception:
        return False

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
    if cand:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fs = {ex.submit(probe, u): (n, u, g) for n, u, g in cand}
            for f in as_completed(fs):
                n, u, g = fs[f]
                try:
                    if f.result():
                        ok.append((n, u, g))
                except Exception:
                    pass
    print(f"[测活通过] {len(ok)} 条")

    if not ok:
        if os.path.exists(OUTPUT):
            print("[兜底] 测活为0，保留上次 live.m3u，不覆盖")
        else:
            print("[致命] 无可用源且无历史文件")
        return

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

    if os.path.exists(OUTPUT):
        os.replace(OUTPUT, BACKUP)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[最终频道-含线路] {len(final)} 条")
    print(f"[去重后台数] {len(by_name)} 个")

if __name__ == "__main__":
    main()
