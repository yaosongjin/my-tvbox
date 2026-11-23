#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 live.m3u —— 同台保留多条线路，供端上手动选源 / 自动回退。

- 每个频道最多保留 MAX_LINES 条测活通过的源
- 线路按"域名稳定性"排序：稳域名在前
- 输出名带 线路1/2/3，同名不同 URL 在影视仓里可手动切换
- 不保底、不强制覆盖，全来自上游真测活
"""

import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================== 配置 ==================
UPSTREAMS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/iptv.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/iptv.m3u",
]

OUTPUT = "live.m3u"
TIMEOUT = (3, 6)
MAX_WORKERS = 28
HARD_LIMIT = 300
MAX_LINES = 3           # 同台最多保留线路数

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# 广告/错误黑名单
BAD_MARKERS = (
    b"advertise", b"advert", b"jump", b"landing",
    b"403 forbidden", b"not found",
    b"you don't have authorization",
    b"\xe6\x8b\x92\xe7\xbb\x9d\xe8\xae\xbf\xe9\x97\xae",  # 拒绝访问
    b"\xe6\x97\xa0\xe6\x9d\x83\xe9\x99\x90",              # 无权限
    b"\xe5\xb9\xbf\xe5\x91\x8a",                          # 广告
    b"\xe8\xb7\xb3\xe8\xbd\xac",                          # 跳转
    b"400-",                                              # 广告热线特征
)

# 已知较稳域名（排前面当主源）
PREFERRED_DOMAINS = (
    "fanmingming", "gaoma", "iptv-org",
    "live.cctv.cn", "cctv.com",
)

# ================== 清洗 ==================
_CLEAN_PATTERNS = [
    re.compile(r"\[[^\]]*\]"),
    re.compile(r"\([^\)]*\)"),
    re.compile(r"\bHD\b", re.I),
    re.compile(r"\bFHD\b", re.I),
    re.compile(r"\bUHD\b", re.I),
    re.compile(r"高清|超清|标清|蓝光|4K|8K", re.I),
    re.compile(r"\b\d{3,4}[pi]\b", re.I),
]

def clean_name(name):
    s = name.strip()
    for p in _CLEAN_PATTERNS:
        s = p.sub("", s)
    return re.sub(r"\s+", " ", s).strip(" -_|·")

def norm(name):
    return re.sub(r"[\s\-_·|]+", "", clean_name(name)).lower()

# ================== 分组 ==================
PROVINCE_EN = {
    "hunan", "zhejiang", "jiangsu", "anhui", "beijing", "shanghai",
    "guangdong", "shenzhen", "tianjin", "chongqing", "shandong",
    "henan", "hebei", "hubei", "sichuan", "fujian", "jiangxi",
    "liaoning", "heilongjiang", "jilin", "shanxi", "shaanxi",
    "gansu", "qinghai", "yunnan", "guizhou", "guangxi", "hainan",
    "xinjiang", "xizang", "tibet", "ningxia", "neimenggu", "innermongolia",
}
HMT_KEYWORDS = ("凤凰", "翡翠", "明珠", "tvb", "凤凰卫视", "翡翠台", "明珠台")

def match_group(name):
    s = clean_name(name)
    low = s.lower()
    if "cctv" in low or s.startswith("央视"):
        return "央视频道"
    for kw in HMT_KEYWORDS:
        if kw in low:
            return "卫视频道"
    if "卫视" in s:
        return "卫视频道"
    m = re.fullmatch(r"([A-Za-z ]+?)\s*TV", s)
    if m and m.group(1).replace(" ", "").lower() in PROVINCE_EN:
        return "卫视频道"
    return "地方台"

# ================== 解析 ==================
_ATTR_NAME = re.compile(r'#EXTINF:[^,]*,(.*)$', re.I)

def parse(text):
    out, pending = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            m = _ATTR_NAME.search(line)
            pending = m.group(1).strip() if m else ""
        elif line and not line.startswith("#") and line.lower().startswith("http") and pending:
            out.append((pending, line))
            pending = None
    return out

# ================== 测活 ==================
def alive(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, stream=True, headers=UA)
        try:
            if r.status_code != 200:
                return False
            head = next(r.iter_content(1024), b"")
        finally:
            r.close()
        low = head.lower()
        if b"#extm3u" not in low and b".m3u8" not in low:
            return False
        for bad in BAD_MARKERS:
            if bad in low:
                return False
        return True
    except Exception:
        return False

def preferred_rank(url):
    """稳域名在前，主源优先"""
    host = url.lower()
    for i, d in enumerate(PREFERRED_DOMAINS):
        if d in host:
            return i
    return len(PREFERRED_DOMAINS)

# ================== 拉取上游 ==================
def fetch(url):
    try:
        r = requests.get(url, timeout=(5, 12), stream=True, headers=UA)
        try:
            if r.status_code == 200:
                body = next(r.iter_content(4096), b"")
                if b"#EXTM3U" in body[:200].upper():
                    return body.decode("utf-8", "ignore")
        finally:
            r.close()
    except Exception:
        pass
    return None

# ================== 主流程 ==================
def main():
    raw_items = []
    for up in UPSTREAMS:
        print(f"[拉取] {up}")
        txt = fetch(up)
        if txt:
            items = parse(txt)
            print(f"  ✓ 解析 {len(items)} 条")
            raw_items.extend(items)
        else:
            print(f"  ✗ 失败")
    print(f"[原始] 共 {len(raw_items)} 条")

    # 收集所有 (name, url)，不做台级去重
    all_items = []
    for name, url in raw_items:
        cname = clean_name(name)
        if cname:
            all_items.append((cname, url, match_group(cname)))
    all_items = all_items[:HARD_LIMIT]
    print(f"[候选] {len(all_items)} 条")

    # 并发测活
    alive_items = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(alive, url): item for item in all_items}
        for f in as_completed(futs):
            item = futs[f]
            try:
                if f.result():
                    alive_items.append(item)
            except Exception:
                pass
    print(f"[测活通过] {len(alive_items)} 条")

    # 同台聚合，最多 MAX_LINES 条，按稳域名排序
    by_name = {}
    for cname, url, grp in alive_items:
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

    # 写 m3u：同台多线路，名字带 线路N
    lines = ["#EXTM3U"]
    last_base = None
    line_no = 0
    for cname, url, grp in final:
        base = clean_name(cname)
        if base == last_base:
            line_no += 1
        else:
            line_no = 1
            last_base = base
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
