# -*- coding: utf-8 -*-
"""
live.py —— 生成影视仓 / TVBox 用的直播源 live.m3u

本版修掉的问题（对应"点 CCTV3 出来是卖药广告、偶尔又正常"）：
1. 旧版 rank() 判断的是"流地址里有没有 cctv 字样"，可那个字符串是流地址，
   不是上游列表名。结果：把自己把文件命名成 cctv3.m3u8 的灰产转播服务器排到
   第 1 条（影视仓默认只播第 1 条），而运营商正规源排在 2、3 条。现在改为按
   "上游可信度 + 流本身是否真在直播"排序。
2. 旧版 probe() 只要地址能返回 #EXTM3U 就算活：master 里子流全死的、以及
   带 #EXT-X-ENDLIST 的点播文件（广告/电影循环）统统通过。现在做三层校验：
   能拿到节目单 → 最后一个切片真的能下载 → 不是已经结束的点播文件 / 不是
   定格不动的僵尸源。
3. 旧版靠显示名归并，'CCTV-3'、'CCTV3高清'、'中央电视台综艺频道' 会变成三个
   不同频道，线路被打散。现在统一映射成规范名（CCTV3）后再归并。
4. 新增交叉校验：同一个地址在不同上游被标成不同频道 → 视为贴错标签，丢弃；
   地址里写着 cctv5 却标成 CCTV3 → 丢弃。这类"换台"在免费源里非常常见。
5. 新增"终审"：间隔十几秒抓两次节目单，只有切片真的在往前滚才算在直播。广告 /
   电影循环源是固定文件（要么带 ENDLIST，要么序列不动），一律丢弃，不再留作备胎。
6. 可信度只影响排序，不再一票否决：低可信上游的源只要通过终审仍然会用，但会
   在日志里点名为 [弱源]。上一版把低可信源一律过滤，主上游一挂央视就整体清零。
7. 上游列表会被体检：返回的条数太少、或里面一条央视都没有，就判定为失效并按最差
   权重处理，同时打印 [上游失效]。fanmingming 的 repo 路径 2026-11 变成 404，
   正是这条静默故障让央视整组消失的。
8. 只依赖本文件，不需要任何额外的配置文件。
"""

import os
import re
import sys
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

try:                      # 关掉"自签证书"的告警刷屏
    import urllib3

    urllib3.disable_warnings()
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "live.m3u")
BACKUP = os.path.join(ROOT, "live.m3u.bak")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "*/*"}

# (上游地址, 可信度) 可信度数字越小越可靠
# 2026-11 实测：fanmingming/live 仓库里已经没有 tv/iptv.m3u 了（raw 直接 404，
# 他的 tv/ 目录只剩台标 png），列表搬到了自己的域名 live.fanmingming.cn。
# 所以这里同时挂多个来源，任何一个挂掉都不该让某个频道组整体消失。
UPSTREAMS = [
    ("https://live.fanmingming.cn/tv/m3u/iptv.m3u", 0),
    ("https://live.fanmingming.cn/tv/m3u/ipv6.m3u", 1),
    ("https://cdn.jsdelivr.net/gh/YueChan/Live@main/IPTV.m3u", 1),
    ("https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u", 1),
    ("https://cdn.jsdelivr.net/gh/vbskycn/iptv@master/tv/iptv4.m3u", 2),
    ("https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u", 2),
    # 社区爬的，贴错标签比例高，只当最后手段
    ("https://iptv-org.github.io/iptv/countries/cn.m3u", 6),
]

MAX_WORKERS = 24
PER_CH_CANDIDATES = 6      # 每个频道最多留几个地址去测（上游多了，候选也该多留）
# 每个频道输出几条线路。默认 1 条：影视仓在第一条播失败时会静默切到下一条，
# 多给几条等于给它机会跳到灰产源上（这正是"有时正常有时出广告"的来源之一）
PER_CH_LINES = int(os.environ.get("PER_CH_LINES", "1"))
PLAYLIST_LIMIT = 512 * 1024
SEG_BYTES = 4096
STALE_SECONDS = 1800       # 切片超过 30 分钟没更新才算定格（太严会误杀打包源）
# 通过校验的频道占比低于这个值只报警、不中止（写出去的都是验证过的，安全）
MIN_COVERAGE = float(os.environ.get("MIN_COVERAGE", "0.5"))

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_BRACKET = re.compile(r"[\[【(（][^\]】)）]*[\]】)）]")
_LINE_NO = re.compile(r"(线路|备用|源)\s*\d+")
_QUALITY = re.compile(
    r"(超高清|蓝光|超清|高清|标清|流畅|4K|8K|1080P|720P|540P|FHD|UHD|HD|SD|"
    r"H\.?265|HEVC|AVC|50FPS|60FPS|30FPS|25FPS)",
    re.I,
)
_CCTV_NUM = re.compile(r"cctv[\-_ ]?(\d{1,2})", re.I)

_CN2CCTV = [
    ("体育赛事", "5+"),
    ("中文国际", "4"),
    ("国防军事", "7"),
    ("社会与法", "12"),
    ("农业农村", "17"),
    ("奥林匹克", "16"),
    ("综合", "1"),
    ("财经", "2"),
    ("综艺", "3"),
    ("体育", "5"),
    ("电影", "6"),
    ("电视剧", "8"),
    ("纪录", "9"),
    ("科教", "10"),
    ("戏曲", "11"),
    ("新闻", "13"),
    ("少儿", "14"),
    ("音乐", "15"),
]


def log(*args):
    print(*args, flush=True)


# ---------------------------------------------------------------- 频道名归一化

def canon(name):
    """把各种写法的频道名压成唯一标识：CCTV-3 / CCTV3 高清 / 中央电视台综艺频道 → CCTV3"""
    s = (name or "").strip()
    if not s:
        return ""
    # 4K/8K 要先去判，否则 "CCTV4K" 里的 4K 会被当作画质后缀删掉，退化成 "CCTV"
    raw = re.sub(r"\s+", "", s).upper()
    if "CCTV4K" in raw or re.match(r"^CCTV[\-_]?4K", raw):
        return "CCTV4K"
    if "CCTV8K" in raw or re.match(r"^CCTV[\-_]?8K", raw):
        return "CCTV8K"

    s = _BRACKET.sub("", s)
    s = _LINE_NO.sub("", s)
    s = _QUALITY.sub("", s)
    s = s.replace("－", "-").replace("—", "-").replace("　", "")
    s = re.sub(r"[\s\-_·•]+", "", s)
    if not s:
        return ""

    up = s.upper()
    m = re.match(r"^CCTV(\d{1,2})(\+|PLUS)?", up)
    if m:
        # CCTV-4欧洲 / CCTV-4美洲 是海外版，内容跟国内 CCTV4 不是一路，必须分开
        tail = up[m.end():]
        for k in ("欧洲", "美洲", "亚洲", "海外"):
            if k in tail:
                return "CCTV" + m.group(1) + k
        return "CCTV" + m.group(1) + ("+" if m.group(2) else "")

    # 只有明确是中央台的中文名才做映射，避免把地方"体育频道"也吞进来
    if "中央" in s or "CCTV" in up:
        for kw, num in _CN2CCTV:
            if kw in s:
                return "CCTV" + num

    if "卫视" in s:
        return re.sub(r"(电视台|频道)$", "", s)

    return s


_CCTV_OK = re.compile(r"^CCTV\d{1,2}(\+|PLUS)?(欧洲|美洲|亚洲|海外)?$")
_CCTV_CN = re.compile(r"^CCTV[\u4e00-\u9fa5][\u4e00-\u9fa5&·]{1,9}$")


def valid_ch(ch):
    """挡住假台名：iptv-org 里有一堆 "CCTV+1"、"CCTVStormFootball"、
    "CCTVWomen'sFashion" 这类把境外台挂上 CCTV 前缀的条目，留着只会占位。"""
    if not ch.startswith("CCTV"):
        return True
    if ch in ("CCTV4K", "CCTV8K"):
        return True
    if _CCTV_OK.match(ch):
        return True
    return bool(_CCTV_CN.match(ch))      # 央视付费频道：CCTV怀旧剧场 / CCTV文化精品


def group_of(ch):
    if ch.startswith("CCTV") or ch.startswith("CGTN"):
        return "央视频道"
    if "卫视" in ch:
        return "卫视频道"
    return "地方台"


def ch_order(ch):
    if ch in ("CCTV4K", "CCTV8K"):
        return (0, 90, 0)
    if ch.startswith("CGTN"):
        return (0, 95, 0)
    m = re.match(r"^CCTV(\d{1,2})(\+)?(.*)$", ch)
    if m:
        suffix = m.group(3) or ""
        # 同一台号内部顺序：本台 → 5+ 这类加大版 → 欧洲/美洲等海外版
        rank = 1 if m.group(2) else (2 if suffix else 0)
        return (0, int(m.group(1)), rank)
    if "卫视" in ch:
        return (1, 0, 0)
    return (2, 0, 0)


# ---------------------------------------------------------------- 拉取与解析

def fetch_text(url, timeout=(6, 15), retries=2):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                r.encoding = "utf-8"
                return r.text
            log(f"  拉取失败 {url} -> HTTP {r.status_code}")
        except Exception as e:
            log(f"  拉取失败 {url}: {e}")
        time.sleep(1 + i)
    return None


def parse(text):
    """解析 m3u，保留 tvg-id / tvg-logo，供后面做交叉校验和图标"""
    out = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            tail = line.rsplit(",", 1)[-1].strip()
            name = (attrs.get("tvg-name") or tail or "").strip()
            cur = {
                "name": name,
                "tvg_id": (attrs.get("tvg-id") or "").strip(),
                "logo": (attrs.get("tvg-logo") or "").strip(),
            }
        elif line.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = line
            out.append(cur)
            cur = None
    return out


# ---------------------------------------------------------------- 流校验

def _request(url, headers, timeout, stream=False):
    """正常请求；证书报错时退回不校验（不少地方台/运营商源用自签证书）"""
    try:
        return requests.get(url, headers=headers, timeout=timeout, stream=stream)
    except Exception as e:
        if type(e).__name__ != "SSLError":
            raise
        return requests.get(url, headers=headers, timeout=timeout, stream=stream, verify=False)


def _http_bytes(url, limit=PLAYLIST_LIMIT, timeout=(6, 10), extra=None):
    try:
        r = _request(url, {**HEADERS, **(extra or {})}, timeout, stream=True)
        try:
            if r.status_code >= 400:
                return None
            buf = b""
            for chunk in r.iter_content(4096):
                buf += chunk
                if len(buf) >= limit:
                    break
            return buf
        finally:
            r.close()
    except Exception:
        return None


def _seg_ok(seg_url):
    """最后一个切片能否真的下载（很多源节目单还在、切片已经 404）"""
    try:
        r = _request(
            seg_url,
            {**HEADERS, "Range": f"bytes=0-{SEG_BYTES - 1}"},
            (6, 8),
            stream=True,
        )
        try:
            if r.status_code not in (200, 206):
                return False, None
            first = next(r.iter_content(256), b"") or b""
            lm = r.headers.get("Last-Modified")
        finally:
            r.close()
        if len(first) < 100:
            return False, lm
        if first.lstrip()[:1] == b"<":      # 返回 HTML 错误页
            return False, lm
        return True, lm
    except Exception:
        return False, None


def _stale(last_modified):
    if not last_modified:
        return False
    try:
        ts = parsedate_to_datetime(last_modified).timestamp()
        return (time.time() - ts) > STALE_SECONDS
    except Exception:
        return False


def _media(url, text=None):
    if text is None:
        body = _http_bytes(url, limit=PLAYLIST_LIMIT)
        if not body:
            return None
        text = body.decode("utf-8", "ignore")
    up = text.upper()
    if "#EXTM3U" not in up or "#EXT-X-STREAM-INF" in up:
        return None
    segs = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not segs:
        return None
    ok, lm = _seg_ok(urljoin(url, segs[-1]))
    if not ok:
        return None
    if _stale(lm):
        return None
    return {"state": "endlist" if "#EXT-X-ENDLIST" in up else "live"}


def inspect(url):
    """返回 None（不可用）或 {'state': 'live'|'rawts'|'endlist'}"""
    if not url.lower().startswith(("http://", "https://")):
        return None
    body = _http_bytes(url, limit=PLAYLIST_LIMIT)
    if not body:
        return None
    if body[:1] == b"\x47" and len(body) > 188:      # 裸 TS，无法解析节目单
        return {"state": "rawts"}
    text = body.decode("utf-8", "ignore")
    if "#EXTM3U" not in text.upper():
        return None
    if "#EXT-X-STREAM-INF" in text.upper():
        base = url
        variants = [
            urljoin(base, l.strip())
            for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        for v in variants[:2]:
            r = _media(v)
            if r:
                return r
        return None
    return _media(url, text)


def score(entry):
    """数字越小越优先"""
    s = 0
    st = entry.get("state")
    if st == "live":
        s += 0
    elif st == "rawts":
        s += 12
    elif st == "endlist":        # 点播文件（广告 / 电影循环）只能当备胎
        s += 40
    else:                        # 没通过校验
        s += 200
    s += int(entry.get("trust", 9)) * 4
    if not entry["url"].lower().startswith("https"):
        s += 2
    return s


# ---------------------------------------------------------------- 终审：确认"真的在直播"
#
# 前面的 inspect() 只能证明"这个地址能取到节目单、切片下载得下来"。广告源、电影循环源、
# 台标贴错的源都能通过。真正能拉开差距的一条判据是：直播流的切片必须一直在往前滚。
# 所以这里间隔十几秒抓两次节目单做比对 —— 不需要解码画面，跑得完，也够狠。

def _playlist_text(url):
    body = _http_bytes(url, limit=PLAYLIST_LIMIT)
    if not body:
        return None
    text = body.decode("utf-8", "ignore")
    if "#EXTM3U" not in text.upper():
        return None
    return text


def _resolve(url, text=None, depth=0):
    """master 节目单 → 一条真正带切片的子流；(media_url, text) 或 (None, None)"""
    t = text if text is not None else _playlist_text(url)
    if not t:
        return None, None
    if "#EXT-X-STREAM-INF" not in t.upper():
        return url, t
    if depth > 1:
        return None, None
    variants = [
        urljoin(url, l.strip())
        for l in t.splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    for v in variants[:2]:
        mu, mt = _resolve(v, None, depth + 1)
        if mu:
            return mu, mt
    return None, None


def _snapshot(text, base):
    """(media-sequence, 最后一个切片, targetduration, 是否带 ENDLIST)"""
    up = text.upper()
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:\s*(\d+)", up)
    seq = int(m.group(1)) if m else None
    m = re.search(r"#EXT-X-TARGETDURATION:\s*(\d+)", up)
    tdur = int(m.group(1)) if m else 6
    segs = [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    last = urljoin(base, segs[-1]) if segs else ""
    return seq, last, tdur, ("#EXT-X-ENDLIST" in up)


def _poll(urls, workers):
    """抓一轮基线：{url: (媒体节目单地址, 快照)}"""
    def one(u):
        mu, mt = _resolve(u)
        if not mu:
            return u, None
        return u, (mu, _snapshot(mt, mu))

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, r in ex.map(one, urls):
            out[u] = r
    return out


def _compare(bases, workers):
    """再抓一轮和基线比：{(url): (是否在直播, 原因)}"""
    def one(item):
        u, r = item
        if not r:
            return u, (False, "nofetch")
        mu, snap0 = r
        t = _playlist_text(mu)
        if not t:
            return u, (False, "nofetch")
        seq1, last1, _, end1 = _snapshot(t, mu)
        seq0, last0, _, end0 = snap0
        if end0 or end1:                      # 已经结束的点播文件 = 广告 / 电影循环
            return u, (False, "endlist")
        if seq0 is not None and seq1 is not None and seq1 != seq0:
            return u, (True, "seq")
        if last1 and last1 != last0:
            return u, (True, "seg")
        return u, (False, "static")

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, r in ex.map(one, bases.items()):
            out[u] = r
    return out


def confirm_many(urls, workers=MAX_WORKERS):
    """间隔若干秒抓两次节目单：真直播的切片必然往前滚；广告 / 电影循环源要么带
    ENDLIST，要么序列不动，两者都判死。落榜的再复核一轮，避免 CDN 缓存了节目单
    造成误杀（不少 IPTV 中转服务器的 m3u8 有 10~30 秒缓存）。

    已知边界：它证明不了"画面就是这个台"。一条 24 小时卖药的直播流同样会通过，
    这种只能靠上游可信度排序去压，代码层面验不出来。
    """
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return {}

    bases = _poll(urls, workers)
    tdurs = [r[1][2] for r in bases.values() if r]
    wait = max(8, min(14, (max(tdurs) if tdurs else 6) + 3))
    log(f"[终审] 间隔 {wait}s 抓两轮节目单，确认是否真在直播（{len(urls)} 条）…")
    time.sleep(wait)
    res = _compare(bases, workers)

    retry = [u for u, (ok, why) in res.items() if not ok and why != "endlist"]
    if retry:
        log(f"[终审] {len(retry)} 条这轮没看出在动，再等一轮复核（排除节目单被缓存）…")
        b2 = _poll(retry, workers)
        time.sleep(wait)
        for u, (ok, _why) in _compare(b2, workers).items():
            if ok:
                res[u] = (True, "retry")

    passed = sum(1 for ok, _ in res.values() if ok)
    log(f"[终审] 确认在直播 {passed} / {len(res)} 条")
    return {u: ok for u, (ok, _) in res.items()}


# ---------------------------------------------------------------- 主流程

def main():
    log("[启动] live.py")

    entries = []
    seen_up = set()
    dead_up, suspect = [], []
    for up, trust in UPSTREAMS:
        if up in seen_up:
            continue
        seen_up.add(up)
        log(f"[拉取] {up}")
        txt = fetch_text(up)
        if not txt:
            dead_up.append(up)
            continue
        got = parse(txt)
        n_cctv = sum(1 for it in got if canon(it["name"]).startswith("CCTV"))
        log(f"  √ 解析 {len(got)} 条（含央视 {n_cctv} 条，可信度 {trust}）")
        # 一个正常的国内列表不可能一条央视都没有 —— 出现了就说明这个地址已经失效，
        # 只是刚好返回了别的内容（这是上一版央视整体消失的直接原因，必须吵出来）
        if len(got) < 10 or n_cctv == 0:
            log("  [上游异常] 内容不像完整的频道表（条数过少或没有央视），权重按最差处理")
            trust = 9
            suspect.append(up)
        for it in got:
            it["trust"] = trust
            entries.append(it)
    log(f"[原始] 共 {len(entries)} 条")

    if dead_up or suspect:
        log(f"[上游检查] 抓取失败 {len(dead_up)} 个 / 内容异常 {len(suspect)} 个")
        for u in dead_up:
            log(f"    失效  {u}")
        for u in suspect:
            log(f"    异常  {u}")

    # 1) 归一化频道名
    for it in entries:
        it["ch"] = canon(it["name"])
    entries = [e for e in entries if e.get("ch") and e.get("url")]
    before = len(entries)
    entries = [e for e in entries if valid_ch(e["ch"])]
    if before != len(entries):
        log(f"[假台名] 丢弃 {before - len(entries)} 条挂着 CCTV 前缀的境外台")
    log(f"[归一化后] 共 {len(entries)} 条")

    # 2) 交叉校验：同一地址被不同上游标成不同频道 → 贴错标签，丢弃
    labels = {}
    for e in entries:
        labels.setdefault(e["url"], set()).add(e["ch"])
    conflict = {u for u, s in labels.items() if len(s) > 1}
    if conflict:
        log(f"[标签冲突] 丢弃 {len(conflict)} 个被贴上多个台名的地址")
    entries = [e for e in entries if e["url"] not in conflict]

    # 3) 地址里的台号与标签不符 → 丢弃（服务器把 cctv5 的文件改标成 cctv3 是常见套路）
    drop = 0
    kept = []
    for e in entries:
        m_ch = re.match(r"^CCTV(\d{1,2})", e["ch"])
        m_url = _CCTV_NUM.search(urlparse(e["url"]).path or "")
        if m_ch and m_url and m_ch.group(1) != m_url.group(1):
            drop += 1
            continue
        kept.append(e)
    entries = kept
    if drop:
        log(f"[台号不符] 丢弃 {drop} 个地址内台号与名称不一致的源")

    # 4) 按频道聚合并裁剪候选
    by_ch = {}
    for e in entries:
        d = by_ch.setdefault(e["ch"], {"name": e["ch"], "logo": "", "cands": []})
        if e.get("logo") and not d["logo"]:
            d["logo"] = e["logo"]
        if e["url"] not in [c["url"] for c in d["cands"]]:
            d["cands"].append(e)

    for d in by_ch.values():
        d["cands"].sort(key=score)
        d["cands"] = d["cands"][:PER_CH_CANDIDATES]

    log(f"[频道] 归并后 {len(by_ch)} 个，待测候选 {sum(len(d['cands']) for d in by_ch.values())} 条")

    # 5) 并发测活（只测候选，不测全网）
    tasks = [(d, c) for d in by_ch.values() for c in d["cands"]]
    log(f"[候选] {len(tasks)} 条待测")
    if tasks:
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(inspect, c["url"]): (d, c) for d, c in tasks}
            for f in as_completed(futs):
                d, c = futs[f]
                done += 1
                if done % 100 == 0:
                    log(f"  测活 {done}/{len(tasks)}")
                try:
                    res = f.result()
                except Exception:
                    res = None
                if res:
                    c["state"] = res["state"]
                else:
                    c["state"] = "dead"

    # 6) 组候选池。可信度只用来排序，不用来"一票否决"：
    #    上一版把低可信上游的央视源全部过滤，结果主上游一挂，央视整体清零 ——
    #    用户看到的是"一个中央台都没了"。现在改为：高可信优先，不够就用低可信
    #    但通过终审的顶上去，并在日志里点名，方便判断要不要换上游。
    pool_of = {}
    for ch, d in by_ch.items():
        live_c = [c for c in d["cands"] if c["state"] == "live"]
        raw_c = [c for c in d["cands"] if c["state"] == "rawts"]
        pool_of[ch] = (sorted(live_c, key=score), sorted(raw_c, key=score))

    # 7) 终审：每台最多验前 5 条（高可信的排在前面），时间花在刀刃上
    to_check = []
    for lc, _ in pool_of.values():
        to_check.extend([c["url"] for c in lc[:5]])
    ok_map = confirm_many(to_check)

    out, empty, weak = [], [], []
    for ch, d in by_ch.items():
        lc, rc = pool_of[ch]
        picked = [c for c in lc if ok_map.get(c["url"])]
        if not picked:
            picked = rc[:PER_CH_LINES]       # 裸 TS 无从做序列校验，只在没有确认源时兜底
        if not picked:
            empty.append(ch)
            continue
        if int(picked[0].get("trust", 9)) >= 6:
            weak.append(ch)
            log(f"  [弱源] {ch} 只剩低可信上游的地址可用，未必是本站，慎用")
        out.append((ch, d["name"], d["logo"], picked[:PER_CH_LINES]))

    final = out
    coverage = len(final) / max(1, len(by_ch))
    log(f"[成片率] {coverage:.0%}（{len(final)}/{len(by_ch)} 个频道）")

    # 写出去的每一条都是"确认在直播"的，所以成片率低只报警、不中止：
    # 少几个台，总比多几个广告台好。成片率 0 才说明这次网络完全不通，保留旧文件。
    if by_ch and coverage < MIN_COVERAGE:
        log("=" * 64)
        log(f"[注意] 只有 {coverage:.0%} 的频道通过终审。基本都是当前网络到不了这些源，")
        log("       不是源死了。本次仍会写文件，但只写通过终审的那些。")
        log("       想让未通过的台也出现，只能在国内宽带的机器上重跑：python scripts/live.py")
        log("=" * 64)

    log(f"[可用频道] {len(final)} 个   [无可用源] {len(empty)} 个")
    if empty:
        log("  无源频道: " + ", ".join(sorted(empty)[:40]) + (" ..." if len(empty) > 40 else ""))

    if not final:
        if os.path.exists(OUTPUT):
            log("[兜底] 本次 0 个可用频道，保留上次 live.m3u，不覆盖")
            log("       注意：这条路径说明本次一条都没验证成功（网络不通），旧文件里的台")
            log("       未必干净。请在能正常播放的宽带下跑一次 python scripts/live.py。")
        else:
            log("[致命] 无可用源且无历史文件")
        return 3

    # 8) 排序 + 输出
    final.sort(key=lambda x: (ch_order(x[0]), x[1]))
    lines = ["#EXTM3U"]
    one_line = 0
    for ch, name, logo, picked in final:
        if len(picked) == 1:
            one_line += 1
        for i, c in enumerate(picked, 1):
            disp = name if i == 1 else f"{name} 线路{i}"
            attrs = f'tvg-id="{ch}" tvg-name="{name}" group-title="{group_of(ch)}"'
            if logo:
                attrs += f' tvg-logo="{logo}"'
            lines.append(f"#EXTINF:-1 {attrs},{disp}")
            lines.append(c["url"])

    if os.path.exists(OUTPUT):
        try:
            os.replace(OUTPUT, BACKUP)
        except Exception:
            pass
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    stats = {}
    for ch, _, _, _ in final:
        g = group_of(ch)
        stats[g] = stats.get(g, 0) + 1
    log(f"[分组统计] {stats}")
    cctv_cnt = sum(1 for x in final if x[0].startswith(("CCTV", "CGTN")))
    if cctv_cnt == 0:
        log("[严重] 本次一个中央台都没生成 —— 上游列表大概率全部失效，请看上面 [上游失效] 那几行")
    if weak:
        log(f"[弱源台] {len(weak)} 个：{', '.join(sorted(weak)[:20])}")
    log(f"[完成] 频道 {len(final)} 个（央视 {cctv_cnt}）/ 线路 {len(lines) // 2} 条 / 单线路频道 {one_line} 个")
    log(f"[输出] {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
