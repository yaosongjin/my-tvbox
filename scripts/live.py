#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 live.m3u —— 只保留真正能播的频道，不做任何保底。

流程：
  1. 从 UPSTREAMS 拉取原始 m3u
  2. parse() 解析出 (name, url)
  3. clean_name() 清洗频道名
  4. match_group() 按广电呼号规范分组
  5. alive() 严格测活（真拉流 + 广告黑名单）
  6. 二次确认（隔几秒再测一次，两次都过才保留）
  7. 写 live.m3u（严格只用测活通过的 result）
"""

import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================== 配置 ==================
UPSTREAMS = [
    "https://cdn.jsdelivr.net/gh/iptv-org/iptv@master/streams/cn.m3u",
    "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/cn.m3u",
    "https://cdn.jsdelivr.net/gh/CCarefree/IPTV@main/live.m3u",
]

OUTPUT = "live.m3u"

TIMEOUT = (4, 8)          # (连接, 读取)
MAX_WORKERS = 20          # 并发测活线程数
HARD_LIMIT = 300          # 候选上限，防止跑太久
SECOND_CHECK_DELAY = 3    # 二次确认前等待秒数

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# 广告/错误页黑名单（小写字节）
BAD_MARKERS = (
    b"advertise", b"advert", b"jump", b"landing",
    b"403 forbidden", b"not found",
    b"you don't have authorization",
    b"\xe6\x8b\x92\xe7\xbb\x9d\xe8\xae\xbf\xe9\x97\xae",  # 拒绝访问
    b"\xe6\x97\xa0\xe6\x9d\x83\xe9\x99\x90",              # 无权限
    b"\xe5\xb9\xbf\xe5\x91\x8a",                          # 广告
    b"\xe8\xb7\xb3\xe8\xbd\xac",                          # 跳转
)

# ================== 频道名清洗 ==================
_CLEAN_PATTERNS = [
    re.compile(r"\[[^\]]*\]"),                      # [xxx]
    re.compile(r"\([^\)]*\)"),                      # (xxx)
    re.compile(r"\bHD\b", re.I),
    re.compile(r"\bFHD\b", re.I),
    re.compile(r"\bUHD\b", re.I),
    re.compile(r"高清|超清|标清|蓝光|4K|8K", re.I),
    re.compile(r"\b\d{3,4}[pi]\b", re.I),           # 1080p / 720i
]

def clean_name(name: str) -> str:
    s = name.strip()
    for p in _CLEAN_PATTERNS:
        s = p.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" -_|·")
    return s

def norm(name: str) -> str:
    """用于去重的归一化 key"""
    s = clean_name(name).lower()
    s = re.sub(r"[\s\-_·|]+", "", s)
    return s

# ================== 分组规则 ==================
# 只有这些算「卫视频道」：
#   1. 中文名含「卫视」
#   2. 英文 ^XX TV$ 且 XX 是省名白名单
#   3. 港澳台指定频道
PROVINCE_EN = {
    "hunan", "zhejiang", "jiangsu", "anhui", "beijing", "shanghai",
    "guangdong", "shenzhen", "tianjin", "chongqing", "shandong",
    "henan", "hebei", "hubei", "sichuan", "fujian", "jiangxi",
    "liaoning", "heilongjiang", "jilin", "shanxi", "shaanxi",
    "gansu", "qinghai", "yunnan", "guizhou", "guangxi", "hainan",
    "xinjiang", "xizang", "tibet", "ningxia", "neimenggu", "innermongolia",
}

HMT_KEYWORDS = ("凤凰", "翡翠", "明珠", "tvb", "凤凰卫视", "翡翠台", "明珠台")

def match_group(name: str) -> str:
    s = clean_name(name)
    low = s.lower()

    if "cctv" in low or s.startswith("央视"):
        return "央视频道"

    # 港澳台
    for kw in HMT_KEYWORDS:
        if kw in low:
            return "卫视频道"

    # 中文「卫视」
    if "卫视" in s:
        return "卫视频道"

    # 英文 ^XX TV$
    m = re.fullmatch(r"([A-Za-z ]+?)\s*TV", s)
    if m:
        prov = m.group(1).replace(" ", "").lower()
        if prov in PROVINCE_EN:
            return "卫视频道"

    return "地方台"

# ================== 解析 m3u ==================
_ATTR_NAME = re.compile(r'#EXTINF:[^,]*,(.*)$', re.I)
_ATTR_GROUP = re.compile(r'group-title="([^"]*)"', re.I)

def parse(text: str):
    """返回 [(name, url)]，跳过 #EXTVLCOPT 等指令向下找 http"""
    out = []
    lines = text.splitlines()
    i = 0
    pending_name = None
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            m = _ATTR_NAME.search(line)
            pending_name = m.group(1).strip() if m else ""
        elif line and not line.startswith("#"):
            if line.lower().startswith("http") and pending_name:
                out.append((pending_name, line))
                pending_name = None
            # 其他非 http 行忽略
        i += 1
    return out

# ================== 测活 ==================
def alive(url: str) -> bool:
    try:
        r = requests.get(url, timeout=TIMEOUT, stream=True, headers=UA)
        if r.status_code != 200:
            r.close()
            return False
        head = b""
        try:
            for chunk in r.iter_content(1024):
                head += chunk
                if len(head) >= 1024:
                    break
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

def alive_confirmed(url: str) -> bool:
    """二次确认：两次都过才算活"""
    if not alive(url):
        return False
    time.sleep(SECOND_CHECK_DELAY)
    return alive(url)

# ================== 拉取上游 ==================
def fetch(url: str):
    try:
        r = requests.get(url, timeout=(6, 15), headers=UA)
        if r.status_code == 200 and "#EXTM3U" in r.text[:200].upper():
            return r.text
    except Exception:
        pass
    return None

# ================== 主流程 ==================
def main():
    raw_items = []
    for up in UPSTREAMS:
        print(f"[拉取] {up}")
        txt = fetch(up)
        if not txt:
            print(f"  ✗ 失败")
            continue
        items = parse(txt)
        print(f"  ✓ 解析 {len(items)} 条")
        raw_items.extend(items)

    print(f"[解析原始] 共 {len(raw_items)} 条")

    # 去重 + 清洗 + 分组
    seen = {}
    for name, url in raw_items:
        cname = clean_name(name)
        if not cname:
            continue
        key = norm(cname)
        if key in seen:
            continue
        seen[key] = (cname, url, match_group(cname))

    candidates = list(seen.values())
    if len(candidates) > HARD_LIMIT:
        candidates = candidates[:HARD_LIMIT]
    print(f"[候选] {len(candidates)} 条（去重后）")

    # 测活（并发）
    result = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(alive_confirmed, url): (cname, url, grp)
                for cname, url, grp in candidates}
        for fut in as_completed(futs):
            cname, url, grp = futs[fut]
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if ok:
                result.append((cname, url, grp))

    # 分组统计
    stats = {}
    for _, _, grp in result:
        stats[grp] = stats.get(grp, 0) + 1
    print(f"[分组统计] {stats}")

    # 排序：央视 → 卫视 → 地方台
    order = {"央视频道": 0, "卫视频道": 1, "地方台": 2}
    result.sort(key=lambda x: (order.get(x[2], 9), norm(x[0])))

    # 写文件（严格只用 result）
    lines = ["#EXTM3U"]
    for cname, url, grp in result:
        lines.append(f'#EXTINF:-1 tvg-name="{cname}" group-title="{grp}",{cname}')
        lines.append(url)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[最终频道] {len(result)} 个")
    for cname, url, grp in result:
        print(f"  - [{grp}] {cname}  {url}")

if __name__ == "__main__":
    main()
