# -*- coding: utf-8 -*-
"""
update_vod.py —— 生成影视仓的 ysc.json（只负责点播仓，直播源由 live.py 生成）

相对原版的改动（都是健壮性，不改变点播行为）：
1. 路径改成相对脚本定位，不再依赖运行时的工作目录。
2. 候选里同一 api 只保留一条，避免重复站点。
3. 单个源探测抛异常不再影响整轮；返回体不是 JSON / 不是 dict 时安全跳过。
4. 写入前先写临时文件再替换，避免中途失败留下半个 ysc.json。
5. 把直播源地址注入 lives，让影视仓自动带出直播列表。此处指向 live.txt（不是
   live.m3u）—— 影视仓的「直播地址」只读，只能靠这个字段换，"一个台多条源"全靠它。
6. 探不通的站当天就不写进配置，通了下次自动回来（用户 2026-11-25："不通的时候就不该
   显示，通了再显示就行"）。探测用 (5,10) 超时 + 每个入口重试一次，避免网络抖动
   把活站误判成死站；但判定标准仍然是"真返回了非空片单"才算通，不会放死站进来。
"""

import concurrent.futures as cf
import hashlib
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "config", "sources.json")
OUT = os.path.join(ROOT, "ysc.json")

# 探测超时 (连接, 读取)。原来是 (3, 5)，跨境访问国内采集站，3 秒建连太紧了，
# 网络稍微抖一下就把能用的站判死。放宽到 (5, 10)。
TIMEOUT = (5, 10)
DEEP_CHECK = False
MAX_WORKERS = 8
# 单站总预算（秒）。8 个站并发跑，30 秒足够，超了就先当作不通。
PROBE_BUDGET = 30
# 同一个入口失败后重试几次。设为 0 就是"一次不通就判死"。
RETRY = 1

# 想让影视仓打开就自带直播列表，就把你的 raw 地址填进来。
#
# ★ 2026-11-25 关键修正：影视仓的「直播地址」是**只读**的 —— 它来自这份 json 的
#   lives 字段，配置地址一填，直播地址就自动出现，只能删不能改（用户实测反馈）。
#   所以"一个台只显示一个源"的根子在这里，不在 APP，也不在播放器设置。
#
#   这里必须指向 **live.txt**，不能是 live.m3u：
#     live.txt  同一个台名写多行 → 影视仓按台名累加成"一个台 + 多条线路"
#     live.m3u  每台只写第一条   → 永远只有一个源
#   （m3u 里同名的多条 #EXTINF 影视仓不会合并，会当成几个频道并排显示。）
LIVES_URL = ("https://gh-proxy.com/https://raw.githubusercontent.com/"
             "yaosongjin/my-tvbox/main/live.txt")
LIVES_NAME = "小肥羊的直播"
LIVES_EPG = "https://epg.pw/xmltv/epg_CN.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Connection": "close",
}


def log(*a):
    print(*a, flush=True)


def _ok_code(d):
    if not isinstance(d, dict):
        return False
    c = d.get("code", None)
    if c is None:
        return True
    return str(c).lower() in ("1", "200", "success", "ok", "0")


def _has_list(d):
    if not isinstance(d, dict):
        return False
    lst = d.get("list")
    return isinstance(lst, list) and len(lst) > 0


def alive(api):
    """探活：三个入口逐个试，每个入口失败后再试一次。

    判定标准没变（HTTP 200 + code 正常 + list 非空），只是把"一次不通就判死"
    改成"两次都不通才判死"。跨境访问国内采集站，单次超时、DNS 抖动很常见，
    不重试会凭空少掉几个本来能用的站。真挂了的站重试也一样失败，
    所以不会把死站混进来 —— 通不了就不写进配置，这是硬规则。
    """
    base = api if api.endswith("/") else api + "/"
    probes = [base + "?ac=list", base + "?ac=videolist", base + "?ac=detail"]
    budget_end = time.time() + PROBE_BUDGET
    for p in probes:
        for attempt in range(RETRY + 1):
            if time.time() > budget_end:
                log(f"  budget exceeded {api}")
                return False, None
            try:
                r = requests.get(p, timeout=TIMEOUT, headers=HEADERS)
                if r.status_code != 200:
                    break          # 状态码不对，说明这个入口不通，换下一个
                d = r.json()
                if not (_ok_code(d) and _has_list(d)):
                    break          # 返回体不对，同样换入口
                if DEEP_CHECK:
                    item = d["list"][0]
                    vid = item.get("vod_id") if isinstance(item, dict) else None
                    if vid:
                        try:
                            r2 = requests.get(f"{base}?ac=detail&ids={vid}",
                                              timeout=TIMEOUT, headers=HEADERS)
                            d2 = r2.json()
                            if not (_ok_code(d2) and _has_list(d2)):
                                break
                        except Exception:
                            break
                return True, p
            except Exception as e:
                log(f"  probe fail {p} (第{attempt + 1}次): {e}")
    return False, None


def make_key(api):
    return "s" + hashlib.md5(api.encode()).hexdigest()[:6]


def main():
    log("[启动] update_vod.py")
    try:
        with open(SRC, encoding="utf-8") as f:
            cands = json.load(f).get("candidates", [])
    except Exception as e:
        log(f"[致命] 读不了 {SRC}: {e}")
        return
    if not isinstance(cands, list):
        log("[致命] sources.json 里 candidates 应是数组")
        return

    # 去重按 (api, 站名)，不是只看 api。
    #
    # 为什么：影视仓里"首页左上角的标题"和"搜索页的站点名"是同一个东西 —— 都取
    # site 的 name。用户 2026-11-25 要"首页显示小肥羊的点播台、搜索页显示芒果资源"，
    # 一个 name 做不到，只能让同一个 api 挂两个站名：排在前面那个（searchable=0）
    # 当首页数据源，后面那个（searchable=1）进搜索页。
    uniq, seen = [], set()
    for c in cands:
        if not isinstance(c, dict) or not c.get("api"):
            continue
        sig = (c["api"], c.get("name") or "")
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(c)
    cands = uniq
    log(f"[候选] {len(cands)} 个站名 / {len({c['api'] for c in cands})} 个 api")

    # 同一个 api 只探一次（它可能挂了多个站名），结果按 api 复用
    apis = list(dict.fromkeys(c["api"] for c in cands))

    def probe(api):
        try:
            return api, alive(api)
        except Exception as e:
            log(f"  探测异常 {api}: {e}")
            return api, (False, None)

    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        ok_map = dict(ex.map(probe, apis))

    # 标题站兜底（2026-11-25 用户两次纠正后定的方案）。
    #
    # 背景：影视仓首页左上角的标题取 sites[0].name，没有独立的标题字段。而标题站
    # 同时也是首页数据源，它自己的接口一挂，就会被下面的 DEAD 逻辑删掉，标题跟着消失。
    #
    # 我一开始的做法是"把标题站改挂到另一个看起来更稳的站"—— 用户直接指出这是
    # 换汤不换药：明天那个站挂了照样没标题。所以现在改成"不绑定任何特定站"：
    # 标了 title:true 的站，自己的接口没探通时，自动借用本轮第一个探通的 api。
    #   - 名字、key 都不变 → 标题永远是「小肥羊的点播台」
    #   - 首页内容跟着借用对象走 → 至少有片可看，不会空白
    #   - 它自己的接口恢复后，下一轮自动用回自己的，不需要人工改回来
    # 它 searchable=0，不参与搜索，所以借用的 api 与搜索页里的同名站不会冲突。
    fallback_api = next(
        (c["api"] for c in cands if ok_map.get(c["api"], (False, None))[0]), None)

    sites = []
    for c in cands:
        ok, probe_url = ok_map.get(c["api"], (False, None))
        api = c["api"]
        if not ok and c.get("title") and fallback_api:
            log(f"TITLE {c.get('name')} 自己的接口未通，借用 {fallback_api} 保住首页标题")
            api, probe_url, ok = fallback_api, f"{fallback_api} (借用)", True
        # 通不了就不写进配置（用户 2026-11-25 明确）：
        #   "不通的时候就不该显示，通了再显示就行"
        # 所以这里没有 keep 之类的豁免 —— 探不通的站当天就从搜索页消失，
        # 下次探通了自然回来。代价是搜索页的站数会随网络状况小幅波动，这是要的效果。
        if not ok:
            log(f"DEAD {c.get('name')}  {c['api']}")
            continue
        name = c.get("name") or c["api"]
        # key 带上站名：同一 api 的两个站名必须是两个不同的 key，
        # 否则后一个会把前一个覆盖掉。
        # 注意用 c["api"] 而不是 api 算 key：借用兜底时 api 会变，key 不能跟着变，
        # 否则 APP 里这个站的收藏/历史会失效。
        sites.append({
            "key": c.get("key") or make_key(c["api"] + "|" + name),
            "name": name,
            "type": 1,
            "api": api,
            # searchable=0：这个站只做首页数据源，不参与聚合搜索。
            # 用户 2026-11-25 要求"首页左上角仍显示小肥羊的点播台，但搜索页
            # 别出现它" —— 就是这个开关。不影响点开、不影响播放，只让它在
            # 搜索页的站点列表里消失。
            "searchable": int(c.get("searchable", 1)),
            "filterable": int(c.get("filterable", 1)),
        })
        log(f"OK   {name}  ({probe_url})")

    try:
        with open(OUT, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    old_sites = cfg.get("sites", [])
    if not sites and old_sites:
        log(f"[兜底] 本次 0 活源，保留旧配置 {len(old_sites)} 个源，不覆盖")
        return

    cfg.setdefault("key", "my-tvbox")
    cfg.setdefault("name", "我的影视仓")
    cfg["sites"] = sites
    if LIVES_URL:
        live = {"name": LIVES_NAME, "type": 0, "url": LIVES_URL, "playerType": 1}
        if LIVES_EPG:
            live["epg"] = LIVES_EPG
        cfg["lives"] = [live]
        log(f"[直播] lives 指向 {LIVES_URL.rsplit('/', 1)[-1]}")
    else:
        cfg.setdefault("lives", [])

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)
    log(f"[完成] 写入 {len(sites)} 个活源")


if __name__ == "__main__":
    main()
