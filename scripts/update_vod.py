import json, requests

SRC = "config/sources.json"
OUT = "ysc.json"
TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

def _ok_code(d):
    """兼容 code 为 1 / '1' / 200 / 'success' / 缺失"""
    c = d.get("code", None)
    if c is None:
        return True
    return str(c) in ("1", "200", "success", "ok")

def _has_list(d):
    lst = d.get("list")
    return isinstance(lst, list) and len(lst) > 0

def alive(api):
    api = api.rstrip("/")
    # 依次尝试多种入口，任一通过即算活
    probes = [
        api + "?ac=list",
        api + "?ac=videolist",
        api + "?ac=detail",
    ]
    for p in probes:
        try:
            r = requests.get(p, timeout=TIMEOUT, headers=HEADERS)
            if r.status_code != 200:
                continue
            d = r.json()
            if _ok_code(d) and _has_list(d):
                # 二次校验：任一条有播放地址即可
                for item in d["list"][:5]:
                    vid = item.get("vod_id")
                    if not vid:
                        continue
                    try:
                        r2 = requests.get(
                            f"{api}?ac=detail&ids={vid}",
                            timeout=TIMEOUT, headers=HEADERS)
                        d2 = r2.json()
                        if _ok_code(d2) and _has_list(d2):
                            if d2["list"][0].get("vod_play_url"):
                                return True, p
                    except Exception:
                        continue
                # 二次校验全失败但列表非空，也放行（宽松模式）
                return True, p
        except Exception as e:
            print(f"  probe fail {p}: {e}")
            continue
    return False, None

def main():
    cands = json.load(open(SRC, encoding="utf-8"))["candidates"]
    sites = []
    for i, c in enumerate(cands):
        ok, probe = alive(c["api"])
        if ok:
            sites.append({
                "key": f"s{i+1}",
                "name": c["name"],
                "type": 1,
                "api": c["api"],
                "searchable": 1,
                "filterable": 1
            })
            print(f"OK   {c['name']}  ({probe})")
        else:
            print(f"DEAD {c['name']}")

    cfg = json.load(open(OUT, encoding="utf-8"))
    cfg["sites"] = sites
    json.dump(cfg, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"写入 {len(sites)} 个活源")

if __name__ == "__main__":
    main()
