import json, requests, hashlib, concurrent.futures as cf

SRC = "config/sources.json"
OUT = "ysc.json"
TIMEOUT = 10
DEEP_CHECK = False   # True 时额外校验 ac=detail

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

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
    for p in probes:
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
            print(f"  probe fail {p}: {e}")
    return False, None

def make_key(api):
    return "s" + hashlib.md5(api.encode()).hexdigest()[:6]

def main():
    cands = json.load(open(SRC, encoding="utf-8"))["candidates"]

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
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
            print(f"OK   {c['name']}  ({probe})")
        else:
            print(f"DEAD {c['name']}  {c['api']}")

    try:
        cfg = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg.setdefault("key", "my-tvbox")
    cfg.setdefault("name", "我的影视仓")
    cfg["sites"] = sites
    cfg.setdefault("lives", [])

    json.dump(cfg, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"写入 {len(sites)} 个活源")

if __name__ == "__main__":
    main()
