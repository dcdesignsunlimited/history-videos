#!/usr/bin/env python3
"""Find and download public-domain / CC0 archival photos from Wikimedia Commons.

    python3 pipeline/fetch_photo.py search "Boston molasses disaster"
    python3 pipeline/fetch_photo.py get "File:BostonMolassesDisaster.jpg" <slug>

`search` lists candidate files with their license. `get` refuses anything that is not public domain or CC0,
saves a ~1600 px JPEG to assets/<slug>/<n>.jpg and prints the path and a credit line for the scene.
Wikimedia rate-limits shared cloud IPs, so every request backs off and retries on HTTP 429.
"""
import io, json, os, re, sys, time, urllib.parse, urllib.request

UA = "HistoryVideosBot/1.0 (https://github.com/dcdesignsunlimited/history-videos)"
API = "https://commons.wikimedia.org/w/api.php"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK = re.compile(r"(public domain|^pd\b|^pd-|cc0|no restrictions)", re.I)

def fetch(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or k == tries - 1: raise
            wait = min(int(e.headers.get("Retry-After") or 0) or 15 * (k + 1), 45)
            print(f"rate limited, waiting {wait}s...", file=sys.stderr); time.sleep(wait)

def api(**p):
    p.update(format="json", formatversion="2")
    return json.loads(fetch(API + "?" + urllib.parse.urlencode(p)))

def clean(html): return re.sub(r"<[^>]+>", "", html or "").strip()

def info(titles):
    q = api(action="query", titles="|".join(titles), prop="imageinfo", iiprop="url|extmetadata|size|mime")
    out = []
    for pg in q["query"]["pages"]:
        if "imageinfo" not in pg: continue
        ii = pg["imageinfo"][0]; m = ii.get("extmetadata", {})
        g = lambda k: clean(m.get(k, {}).get("value", ""))
        out.append({"title": pg["title"], "license": g("LicenseShortName"), "artist": g("Artist"), "credit": g("Credit"),
                    "date": g("DateTimeOriginal"), "desc": g("ImageDescription")[:200], "w": ii.get("width"), "h": ii.get("height"),
                    "mime": ii.get("mime"), "url": ii["url"], "page": ii.get("descriptionurl")})
    return out

def search(query, n=12):
    r = api(action="query", list="search", srsearch=query, srnamespace=6, srlimit=n)
    titles = [s["title"] for s in r["query"]["search"]]
    for it in info(titles) if titles else []:
        flag = "OK " if OK.search(it["license"]) else "NO "
        print(f"{flag} {it['title']}  [{it['license']}]  {it['w']}x{it['h']}  {it['date'][:30]}\n     {it['desc'][:150]}")

def get(title, slug):
    it = info([title])
    if not it: sys.exit(f"not found: {title}")
    it = it[0]
    if not OK.search(it["license"]): sys.exit(f"REFUSED: license is '{it['license']}', not public domain / CC0")
    if not (it["mime"] or "").startswith("image/"): sys.exit(f"not an image: {it['mime']}")
    from PIL import Image
    # originals live on upload.wikimedia.org (thumbnails are served from another host); downscale locally
    Image.MAX_IMAGE_PIXELS = None
    url = it["url"].split("?")[0]
    if (it["w"] or 0) > 1920 and not url.lower().endswith((".svg", ".tif", ".tiff")):
        # Wikimedia asks bots to use its standard thumbnail sizes, on the same host as the originals
        base, name = url.rsplit("/", 1); url = base.replace("/commons/", "/commons/thumb/", 1) + f"/{name}/1920px-{name}"
    im = Image.open(io.BytesIO(fetch(url))).convert("RGB")
    if im.width > 1600: im = im.resize((1600, int(im.height * 1600 / im.width)), Image.LANCZOS)
    d = os.path.join(ROOT, "assets", slug); os.makedirs(d, exist_ok=True)
    n = len([f for f in os.listdir(d) if f.endswith(".jpg")]) + 1; path = os.path.join(d, f"{n}.jpg")
    im.save(path, quality=85)
    src = next((x for x in (it["artist"], it["credit"]) if x and "http" not in x and "unknown" not in x.lower() and len(x) < 60), "Wikimedia Commons")
    credits = os.path.join(d, "credits.json"); c = json.load(open(credits)) if os.path.exists(credits) else {}
    c[f"{n}.jpg"] = {"title": it["title"], "page": it["page"], "license": it["license"], "source": src, "date": it["date"]}
    json.dump(c, open(credits, "w"), indent=1)
    print(f"saved {os.path.relpath(path, ROOT)} ({im.width}x{im.height})\ncredit: \"Photo: {src} ({it['license']})\"\npage: {it['page']}")

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "search": search(" ".join(sys.argv[2:]))
    elif len(sys.argv) == 4 and sys.argv[1] == "get": get(sys.argv[2], sys.argv[3])
    else: sys.exit(__doc__)
