import json, os, requests, hashlib, concurrent.futures as cf

SRC = "config/sources.json"
OUT = "ysc.json"
TIMEOUT = (3, 5)          # 收紧，单 probe 最多 5 秒
DEEP_CHECK = False
MAX_WORKERS = 8
PROBE_BUDGET = 12         # 单源最多花 12 秒，超了直接判死

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Connection": "close",
}

def log(*a):
    print(*a, flush=True)

def _ok_code(d):
    c = d.get("code", None)
    if c is None:
        return True
    return str(c).lower() in ("1", "200", "success", "ok", "0")

def _has_list(d):
    lst = d.get("list")
    return isinstance(lst, list) and len(lst) > 0

def alive(api):
    base = api if api.endswith("/") else api + "/"
    probes = [base + "?ac=list", base + "?ac=videolist", base + "?ac=detail"]
    budget_end = None
    for p in probes:
        if budget_end is None:
            import time
            budget_end = time.time() + PROBE_BUDGET
        import time
        if time.time() > budget_end:
            log(f"  budget exceeded {api}")
            break
        try:
            r = requests.get(p, timeout=TIMEOUT, headers=HEADERS)
            if r.status_code != 200:
                continue
            d = r.json()
            if not (_ok_code(d) and _has_list(d)):
                continue
            if DEEP_CHECK:
                item = d["list"][0]
                vid = item.get("vod_id")
                if vid:
                    try:
                        r2 = requests.get(f"{base}?ac=detail&ids={vid}",
                                          timeout=TIMEOUT, headers=HEADERS)
                        d2 = r2.json()
                        if not (_ok_code(d2) and _has_list(d2)):
                            continue
                    except Exception:
                        continue
            return True, p
        except Exception as e:
            log(f"  probe fail {p}: {e}")
    return False, None

def make_key(api):
    return "s" + hashlib.md5(api.encode()).hexdigest()[:6]

def main():
    log("[启动] update_vod.py")
    cands = json.load(open(SRC, encoding="utf-8"))["candidates"]
    log(f"[候选] {len(cands)} 个源")

    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(lambda c: (c, alive(c["api"])), cands))

    sites = []
    for c, (ok, probe) in results:
        if ok:
            sites.append({
                "key": c.get("key") or make_key(c["api"]),
                "name": c["name"],
                "type": 1,
                "api": c["api"],
                "searchable": 1,
                "filterable": 1
            })
            log(f"OK   {c['name']}  ({probe})")
        else:
            log(f"DEAD {c['name']}  {c['api']}")

    try:
        cfg = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        cfg = {}

    old_sites = cfg.get("sites", [])
    if not sites and old_sites:
        log(f"[兜底] 本次 0 活源，保留旧配置 {len(old_sites)} 个源，不覆盖")
        return

    cfg.setdefault("key", "my-tvbox")
    cfg.setdefault("name", "我的影视仓")
    cfg["sites"] = sites
    cfg.setdefault("lives", [])

    json.dump(cfg, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"[完成] 写入 {len(sites)} 个活源")

if __name__ == "__main__":
    main()
