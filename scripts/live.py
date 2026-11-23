import os
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "live.m3u")
BACKUP = os.path.join(ROOT, "live.m3u.bak")
PINNED = os.path.join(ROOT, "config", "pinned.txt")

UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/iptv.m3u",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

MAX_WORKERS = 8
HARD_LIMIT = 200
PROBE_TIMEOUT = 3
MAX_LINES_PER_CH = 3


def log(*args):
    print(*args, flush=True)


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=(5, 10))
        r.encoding = "utf-8"
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        log(f"  拉取失败 {url}: {e}")
    return None


def parse(text):
    out = []
    name = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            m = re.search(r",\s*(.+)$", line)
            name = m.group(1).strip() if m else None
        elif line and not line.startswith("#") and name:
            out.append((name, line))
            name = None
    return out


def clean_name(s):
    s = re.sub(r"\s*线路\d+\s*$", "", s)
    s = re.sub(r"\s*\[.*?\]", "", s)
    s = re.sub(r"\s*\(.*?\)", "", s)
    return s.strip()


def norm(s):
    return re.sub(r"[\s\-_（）()·]", "", s).lower()


def match_group(name):
    u = name.upper()
    if "CCTV" in u or "中央" in name:
        return "央视频道"
    if any(x in name for x in ["卫视", "湖南", "浙江", "江苏", "东方", "北京", "广东"]):
        return "卫视频道"
    return "地方台"


def probe(url):
    try:
        r = requests.get(
            url,
            headers={**HEADERS, "Range": "bytes=0-1024"},
            timeout=(2, PROBE_TIMEOUT),
            stream=True,
        )
        head = ""
        for chunk in r.iter_content(1024):
            head += chunk.decode("utf-8", "ignore")
            break
        r.close()
        low = head.lower()
        return (
            r.status_code < 500
            and ("#extm3u" in low or ".ts" in low or ".m3u8" in low or "#extinf" in low)
        )
    except Exception:
        return False


def rank(url):
    u = url.lower()
    if "cctv" in u or "iptv-org" in u:
        return 0
    if "fanmingming" in u:
        return 1
    if "jsdelivr" in u:
        return 2
    if re.search(r"\d{1,3}(\.\d{1,3}){3}:\d+", u):
        return 4
    return 3


def read_pinned():
    out = []
    if not os.path.exists(PINNED):
        return out
    with open(PINNED, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("http"):
                name = "CCTV"
                m = re.search(r"cctv\s*(\d+)", line, re.I)
                if m:
                    name = f"CCTV{m.group(1)}"
                out.append((name, line, "央视频道"))
    return out


def main():
    log("[启动] live.py")
    pinned = read_pinned()
    log(f"[pinned] {len(pinned)} 条")

    raw = []
    for up in UPSTREAMS:
        log(f"[拉取] {up}")
        txt = fetch(up)
        if txt:
            items = parse(txt)
            log(f"  √ 解析 {len(items)} 条")
            raw.extend(items)
    log(f"[原始] 共 {len(raw)} 条")

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
    log(f"[候选] {len(cand)} 条")

    ok = []
    if cand:
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fs = {ex.submit(probe, u): (n, u, g) for n, u, g in cand}
            for f in as_completed(fs):
                n, u, g = fs[f]
                done += 1
                if done % 50 == 0:
                    log(f"  测活 {done}/{len(cand)}")
                try:
                    if f.result():
                        ok.append((n, u, g))
                except Exception:
                    pass
    log(f"[测活通过] {len(ok)} 条")

    if not ok and not pinned:
        if os.path.exists(OUTPUT):
            log("[兜底] 测活为0且无pinned，保留上次 live.m3u")
        else:
            log("[致命] 无可用源且无历史文件")
        return

    by_name = {}
    for cname, url, grp in ok + pinned:
        key = norm(clean_name(cname))
        by_name.setdefault(key, {"cname": clean_name(cname), "grp": grp, "urls": []})
        if url not in by_name[key]["urls"]:
            by_name[key]["urls"].append(url)

    order = {"央视频道": 0, "卫视频道": 1, "地方台": 2}
    final = []
    for key, data in by_name.items():
        urls = sorted(data["urls"], key=rank)[:MAX_LINES_PER_CH]
        if data["grp"] == "央视频道" and any("cctv1" in norm(u) for u in urls):
            pass
        for u in urls:
            final.append((data["cname"], u, data["grp"]))

    cctv_order = {f"cctv{i}": i for i in range(1, 20)}
    final.sort(
        key=lambda x: (
            order.get(x[2], 9),
            cctv_order.get(norm(x[0]), 99),
            norm(x[0]),
        )
    )

    stats = {}
    for _, _, g in final:
        stats[g] = stats.get(g, 0) + 1
    log(f"[分组统计] {stats}")

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

    log(f"[完成] 最终 {len(final)} 条，后台 {len(by_name)} 个")


if __name__ == "__main__":
    main()
