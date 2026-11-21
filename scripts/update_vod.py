import json, requests, sys

SRC = "config/sources.json"
OUT = "ysc.json"
TIMEOUT = 8
HEADERS = {"User-Agent": "Mozilla/5.0"}

def alive(api):
    try:
        r = requests.get(api + "?ac=list", timeout=TIMEOUT, headers=HEADERS)
        d = r.json()
        if d.get("code") == 1 and d.get("list"):
            # 二次校验：detail 里要有播放字段
            r2 = requests.get(api + "?ac=detail&ids=" + str(d["list"][0]["vod_id"]),
                              timeout=TIMEOUT, headers=HEADERS)
            d2 = r2.json()
            if d2.get("list") and d2["list"][0].get("vod_play_url"):
                return True
    except Exception:
        pass
    return False

def main():
    cands = json.load(open(SRC, encoding="utf-8"))["candidates"]
    sites = []
    for i, c in enumerate(cands):
        if alive(c["api"]):
            sites.append({
                "key": f"s{i+1}",
                "name": c["name"],
                "type": 1,
                "api": c["api"],
                "searchable": 1,
                "filterable": 1
            })
            print("OK  ", c["name"])
        else:
            print("DEAD", c["name"])

    cfg = json.load(open(OUT, encoding="utf-8"))
    cfg["sites"] = sites
    json.dump(cfg, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"写入 {len(sites)} 个活源")

if __name__ == "__main__":
    main()
