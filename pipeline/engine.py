#!/usr/bin/env python3
"""History-short engine: story spec (JSON) -> 1080x1920 narrated, captioned MP4.

Usage:  python3 engine.py SPEC.json OUT_DIR
Writes OUT_DIR/<slug>.mp4 and OUT_DIR/preview.jpg, prints the final duration.
See SPEC_FORMAT.md for the spec format.
"""
import json, math, os, random, subprocess, sys
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1080, 1920, 30
VOICE, SPEED = "bm_george", 1.15
LEAD, GAP = 0.06, 0.22
TAILS = {"hook": 0.4, "end": 1.6}

BG = (14, 22, 33); WHITE = (245, 242, 234); AMBER = (242, 177, 52); RED = (210, 58, 46)
PAPER = (239, 230, 210); INK = (40, 36, 32); INKRED = (150, 30, 22)
LAND = (44, 60, 78); COAST = (86, 110, 134); GREY = (190, 190, 190)
COLORS = {"w": WHITE, "white": WHITE, "a": AMBER, "amber": AMBER, "r": RED, "red": RED}

FD = "/usr/share/fonts/opentype/inter/"
FBLACK, FXB, FBOLD = FD + "Inter-Black.otf", FD + "Inter-ExtraBold.otf", FD + "Inter-Bold.otf"
FMONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
_fc = {}
def F(p, s):
    k = (p, int(s))
    if k not in _fc: _fc[k] = ImageFont.truetype(p, max(4, int(s)))
    return _fc[k]

# Pillow draws text at full strength when the fill alpha is 0; skip fully transparent text instead.
_orig_text = ImageDraw.ImageDraw.text
def _text(self, xy, text, fill=None, *a, **k):
    if isinstance(fill, tuple) and len(fill) == 4 and fill[3] <= 0: return
    return _orig_text(self, xy, text, fill, *a, **k)
ImageDraw.ImageDraw.text = _text

def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def prog(t, a, b): return clamp((t - a) / (b - a)) if b > a else float(t >= a)
def eout(x): x = clamp(x); return 1 - (1 - x) ** 3
def eback(x):
    x = clamp(x); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
def einout(x): x = clamp(x); return 4 * x**3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2
def rgba(c, a): return (c[0], c[1], c[2], int(255 * clamp(a)))
def col(name, default=WHITE): return COLORS.get(name, default) if isinstance(name, str) else tuple(name) if name else default

_BG = None
def bgimg():
    global _BG
    if _BG is None:
        y, x = np.mgrid[0:H, 0:W]
        d = np.sqrt(((x - W / 2) / W) ** 2 + ((y - H * 0.42) / H) ** 2)
        f = np.clip(1 - d * 1.6, 0, 1)
        arr = np.array(BG, float)[None, None] * (1 - f[..., None]) + np.array((26, 40, 58), float)[None, None] * f[..., None]
        _BG = Image.fromarray(arr.astype(np.uint8), "RGB")
    return _BG

# ---------------- shared drawing helpers ----------------
def label(d, xy, text, a=1.0, c=WHITE, size=30, anchor="mm"):
    d.text(xy, text, font=F(FBOLD, size), fill=rgba(c, a), anchor=anchor, stroke_width=4, stroke_fill=rgba(BG, a))

def fit_font(d, text, path, maxsize, maxw):
    s = maxsize
    while s > 20 and d.textlength(text, font=F(path, s)) > maxw: s -= 4
    return s

def caption(d, chunks, t):
    for (a, b, text) in chunks:
        if not text or not (a <= t < b): continue
        words = text.split(); f = F(FBLACK, 70)
        lines, cur = [], []
        for w in words:
            if cur and d.textlength(" ".join(cur + [w]), font=f) > 920: lines.append(cur); cur = [w]
            else: cur.append(w)
        if cur: lines.append(cur)
        ap = eout(prog(t, a, a + 0.18)); dy = (1 - ap) * 24; lh = 88
        y0 = 1330 - (len(lines) - 1) * lh / 2 + dy
        span = (b - a) * 0.85
        idx = int(clamp((t - a) / span, 0, 0.9999) * len(words)) if span > 0 else 0
        sp = d.textlength(" ", font=f); wi = 0
        for li, ln in enumerate(lines):
            ws = [d.textlength(w, font=f) for w in ln]
            x = W / 2 - (sum(ws) + sp * (len(ln) - 1)) / 2
            for w, ww in zip(ln, ws):
                d.text((x, y0 + li * lh), w, font=f, fill=rgba(AMBER if wi == idx else WHITE, ap), anchor="lm",
                       stroke_width=7, stroke_fill=(0, 0, 0, int(255 * ap)))
                x += ww + sp; wi += 1

def stamp_img(text, size=64, c=RED, angle=-8, pad=28):
    f = F(FBLACK, size)
    tw = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(text, font=f)
    w, h = int(tw + pad * 2), int(size * 1.35 + pad)
    im = Image.new("RGBA", (w + 20, h + 20), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([10, 10, w + 10, h + 10], radius=10, outline=c + (255,), width=8)
    d.text(((w + 20) / 2, (h + 20) / 2), text, font=f, fill=c + (255,), anchor="mm")
    arr = np.array(im); m = np.random.default_rng(3).random(arr.shape[:2]) < 0.10
    arr[..., 3][m] = (arr[..., 3][m] * 0.35).astype(np.uint8)
    return Image.fromarray(arr).rotate(angle, expand=True, resample=Image.BICUBIC)

def paste(img, obj, cx, cy, s=1.0, a=1.0):
    if a <= 0 or s <= 0: return
    o = obj if abs(s - 1) < 1e-3 else obj.resize((max(1, int(obj.width * s)), max(1, int(obj.height * s))), Image.BICUBIC)
    if a < 0.999:
        o = o.copy(); o.putalpha(o.getchannel("A").point(lambda v: int(v * a)))
    img.paste(o, (int(cx - o.width / 2), int(cy - o.height / 2)), o)

def shadowed(w, h, fill, radius=14, blur=18, off=14):
    im = Image.new("RGBA", (w + 80, h + 80), (0, 0, 0, 0)); sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([40, 40 + off, 40 + w, 40 + h + off], radius=radius, fill=(0, 0, 0, 150))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(im).rounded_rectangle([40, 40, 40 + w, 40 + h], radius=radius, fill=fill)
    return im

def overlay_stamp(img, sc, t, D, ctx, y=1090):
    st = sc.get("stamp")
    if not st: return
    at = st.get("at", 0.55) * D; b = prog(t, at, at + 0.18)
    if b <= 0: return
    key = "stamp_" + st["text"]
    if key not in ctx: ctx[key] = stamp_img(st["text"], st.get("size", 60), col(st.get("color", "red"), RED), st.get("angle", -6))
    paste(img, ctx[key], W / 2, st.get("y", y), 1.6 - 0.6 * eout(b), b)

def wrap(text, n):
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > n: out.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    if cur: out.append(cur)
    return out

def typed(d, xy, text, t, t0, cps, font, fill, anchor="lm"):
    n = int(clamp((t - t0) * cps, 0, len(text)))
    if n: d.text(xy, text[:n], font=font, fill=fill, anchor=anchor)

# ---------------- atmosphere particles ----------------
def atmosphere(d, kind, t):
    if not kind or kind == "none": return
    rng = random.Random(11)
    if kind == "snow":
        for i in range(120):
            x0, sp, r = rng.uniform(0, W), rng.uniform(40, 110), rng.choice([2, 3, 4])
            y = (rng.uniform(0, H) + t * sp) % H; x = x0 + math.sin(t * 0.8 + i) * 20
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 140))
    elif kind == "embers":
        for i in range(80):
            x0, sp, r = rng.uniform(0, W), rng.uniform(60, 160), rng.choice([2, 3])
            y = H - ((rng.uniform(0, H) + t * sp) % H); x = x0 + math.sin(t * 1.3 + i) * 25
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, rng.randint(120, 190), 40, rng.randint(120, 220)))
    elif kind == "rain":
        for i in range(140):
            x0, sp = rng.uniform(-200, W), rng.uniform(900, 1400)
            y = (rng.uniform(0, H) + t * sp) % H; x = x0 + y * 0.12
            d.line([(x, y), (x + 6, y + 40)], fill=(170, 190, 220, 90), width=2)
    elif kind == "dust":
        for i in range(70):
            x0, y0, r = rng.uniform(0, W), rng.uniform(0, H), rng.choice([1, 2, 3])
            x = (x0 + t * rng.uniform(5, 25)) % W; y = y0 + math.sin(t * 0.6 + i) * 15
            d.ellipse([x - r, y - r, x + r, y + r], fill=(230, 210, 170, 70))

def sea(d, t, water=1010):
    pts = [(0, H)] + [(x, water + math.sin(x / 70 + t * 2.2) * 8) for x in range(0, W + 20, 20)] + [(W, H)]
    d.polygon(pts, fill=(12, 28, 44))
    for row in range(1, 9):
        yb = water + row * 46
        d.line([(x, yb + math.sin(x / (60 + row * 6) + t * (1.6 + row * .1) + row) * (5 + row)) for x in range(0, W + 20, 20)],
               fill=(60, 90, 120, max(0, 110 - row * 10)), width=3)

_NIGHT = None
def night():
    global _NIGHT
    if _NIGHT is None:
        y = np.linspace(0, 1, H)[:, None]
        arr = np.array((5, 10, 18), float) * (1 - y[..., None]) + np.array((24, 40, 60), float) * y[..., None]
        img = Image.fromarray(np.repeat(arr, W, axis=1).astype(np.uint8), "RGB"); d = ImageDraw.Draw(img, "RGBA")
        rng = random.Random(7)
        for _ in range(140):
            x, yy, r = rng.randint(0, W), rng.randint(0, 950), rng.choice([1, 1, 2])
            d.ellipse([x - r, yy - r, x + r, yy + r], fill=(255, 255, 255, rng.randint(60, 200)))
        g = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ImageDraw.Draw(g).ellipse([700, 330, 960, 590], fill=(220, 225, 200, 60))
        g = g.filter(ImageFilter.GaussianBlur(40)); img.paste(g, (0, 0), g)
        ImageDraw.Draw(img).ellipse([780, 410, 880, 510], fill=(235, 232, 210))
        _NIGHT = img
    return _NIGHT

# ---------------- map ----------------
_GEO = {}
def geo(name):
    if name not in _GEO:
        _GEO[name] = json.load(open(os.path.join(HERE, "data", name + ".geojson")))["features"]
    return _GEO[name]

def rings(geom):
    if geom["type"] == "Polygon": return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon": return [p[0] for p in geom["coordinates"]]
    return []

class Proj:
    def __init__(self, bbox):
        lon0, lat0, lon1, lat1 = bbox
        self.mlon, self.mlat = (lon0 + lon1) / 2, (lat0 + lat1) / 2
        self.kx = math.cos(math.radians(self.mlat))
        self.s = min(1000 / ((lon1 - lon0) * self.kx), 640 / (lat1 - lat0))
    def __call__(self, lon, lat): return (540 + (lon - self.mlon) * self.kx * self.s, 660 - (lat - self.mlat) * self.s)

def country_rings(name, P):
    for f in geo("ne_50m_admin_0_countries"):
        p = f["properties"]
        if name.lower() in (str(p.get("NAME", "")).lower(), str(p.get("ADMIN", "")).lower(), str(p.get("NAME_LONG", "")).lower()):
            return [[P(*c) for c in r] for r in rings(f["geometry"])]
    return []

def make_map(sc):
    P = Proj(sc["bbox"]); img = bgimg().copy(); d = ImageDraw.Draw(img, "RGBA")
    def visible(pts):
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return max(xs) > -50 and min(xs) < W + 50 and max(ys) > 200 and min(ys) < 1150
    for f in geo("ne_50m_land"):
        for r in rings(f["geometry"]):
            pts = [P(*c) for c in r]
            if len(pts) > 2 and visible(pts): d.polygon(pts, fill=LAND)
    for f in geo("ne_50m_admin_0_countries"):
        for r in rings(f["geometry"]):
            pts = [P(*c) for c in r]
            if len(pts) > 2 and visible(pts): d.line(pts + [pts[0]], fill=(86, 110, 134, 150), width=2)
    edge = Image.new("L", (W, H), 0); ed = ImageDraw.Draw(edge); top, bot = 300, 1010
    for i in range(70):
        a = int(255 * (1 - i / 70)); ed.line([(0, top + i), (W, top + i)], fill=a); ed.line([(0, bot - i), (W, bot - i)], fill=a)
    ed.rectangle([0, 0, W, top], fill=255); ed.rectangle([0, bot, W, H], fill=255)
    img.paste(bgimg(), (0, 0), edge)
    d = ImageDraw.Draw(img, "RGBA")
    if sc.get("header"): d.text((W / 2, 240), sc["header"], font=F(FXB, fit_font(d, sc["header"], FXB, 46, 1000)), fill=AMBER, anchor="mm")
    return img, P

def bez(p0, p1, p2, n=60):
    return [((1-t)**2*p0[0]+2*(1-t)*t*p1[0]+t*t*p2[0], (1-t)**2*p0[1]+2*(1-t)*t*p1[1]+t*t*p2[1]) for t in [i/n for i in range(n+1)]]

def arrow(d, pts, frac, c, width=10):
    if frac <= 0: return
    seg = pts[:max(2, int(len(pts) * frac))]
    d.line(seg, fill=c, width=width, joint="curve")
    (x1, y1), (x2, y2) = seg[-2], seg[-1]; ang = math.atan2(y2 - y1, x2 - x1); L = width * 3.2
    d.polygon([(x2 + math.cos(ang) * L * .5, y2 + math.sin(ang) * L * .5), (x2 + math.cos(ang + 2.6) * L, y2 + math.sin(ang + 2.6) * L),
               (x2 + math.cos(ang - 2.6) * L, y2 + math.sin(ang - 2.6) * L)], fill=c)

def ring(d, c, t, colr, r0=20, r1=110, period=1.4):
    for k in range(2):
        ph = ((t / period) + k * 0.5) % 1.0; r = r0 + (r1 - r0) * ph
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=rgba(colr, 0.8 * (1 - ph)), width=5)

def s_map(sc, t, D, ctx):
    if "base" not in ctx: ctx["base"], ctx["P"] = make_map(sc)
    img, P = ctx["base"].copy(), ctx["P"]
    if t < 0.4: img = Image.blend(bgimg(), img, eout(t / 0.4))
    d = ImageDraw.Draw(img, "RGBA")
    for c in sc.get("countries", []):
        a = prog(t, c.get("at", 0.3) * D, c.get("at", 0.3) * D + 0.4)
        if a <= 0: continue
        key = "cr_" + c["name"]
        if key not in ctx: ctx[key] = country_rings(c["name"], P)
        g = 0.45 + 0.3 * math.sin(t * 5); cc = col(c.get("color", "amber"), AMBER)
        for r in ctx[key]: d.polygon(r, fill=rgba(cc, a * g), outline=rgba(cc, a))
    for l in sc.get("labels", []):
        label(d, P(l["lon"], l["lat"]), l["text"], 0.8 * prog(t, l.get("at", 0.05) * D, l.get("at", 0.05) * D + 0.4), WHITE, 28)
    for a_ in sc.get("arrows", []):
        a0 = a_.get("at", 0.3) * D; dur = a_.get("dur", 0.25) * D
        p0, p2 = P(*a_["from"]), P(*a_["to"]); cv = a_.get("curve", 0.25)
        mx, my = (p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2; dx, dy = p2[0] - p0[0], p2[1] - p0[1]
        p1 = (mx - dy * cv, my + dx * cv)
        cc = col(a_.get("color", "red"), RED)
        arrow(d, bez(p0, p1, p2), eout(prog(t, a0, a0 + dur)), cc + (255,), 11)
        if a_.get("label") and t > a0 + dur:
            label(d, (p2[0], p2[1] - 50), a_["label"], prog(t, a0 + dur, a0 + dur + 0.4), WHITE, 30)
    for p in sc.get("pins", []):
        a0 = p.get("at", 0.1) * D
        if t < a0: continue
        xy = P(p["lon"], p["lat"]); cc = col(p.get("color", "amber"), AMBER)
        ring(d, xy, t, cc, 10, 80)
        r = 12 * eback(prog(t, a0, a0 + 0.3)); d.ellipse([xy[0] - r, xy[1] - r, xy[0] + r, xy[1] + r], fill=cc)
        if p.get("label"):
            dx, dy = p.get("label_offset", [0, 55])
            label(d, (xy[0] + dx, xy[1] + dy), p["label"], prog(t, a0, a0 + 0.4), cc if p.get("label_color") else WHITE, 34)
    overlay_stamp(img, sc, t, D, ctx)
    if sc.get("big"):
        bg = sc["big"]; a0 = bg.get("at", 0.5) * D; pp = eout(prog(t, a0, a0 + 0.3))
        if pp > 0: d.text((W / 2, 1085), bg["text"], font=F(FBLACK, fit_font(d, bg["text"], FBLACK, 110, 1000)), fill=rgba(col(bg.get("color", "w")), pp), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0, int(255 * pp)))
    return img, d

# ---------------- other scene types ----------------
def s_hook(sc, t, D, ctx):
    img = bgimg().copy(); d = ImageDraw.Draw(img, "RGBA"); atmosphere(d, sc.get("atmosphere"), t)
    if sc.get("tag"): label(d, (W / 2, 650), sc["tag"], eout(prog(t, 0, 0.4)), AMBER, 40)
    lines = sc["lines"]; y = 790; t0 = 0.2
    for i, ln in enumerate(lines):
        c = col(ln.get("c", "w")); last = i == len(lines) - 1
        sz = fit_font(d, ln["t"], FBLACK, 175 if last else 150, 1010); p = prog(t, t0, t0 + 0.28)
        if p > 0:
            s = 1.35 - 0.35 * eout(p); sh = math.sin(t * 90) * 10 * (t0 + 0.55 - t) / 0.27 if last and t0 + 0.28 < t < t0 + 0.55 else 0
            d.text((W / 2 + sh, y), ln["t"], font=F(FBLACK, sz * s), fill=rgba(c, eout(p)), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0, int(200 * eout(p))))
        if last:
            u = eout(prog(t, t0 + 0.6, t0 + 1.0))
            if u > 0:
                hw = d.textlength(ln["t"], font=F(FBLACK, sz)) / 2
                d.rectangle([W / 2 - hw, y + sz * 0.62, W / 2 - hw + 2 * hw * u, y + sz * 0.62 + 16], fill=RED)
        y += sz * 1.05 if i < len(lines) - 2 else sz * 1.12; t0 += 0.4
    if t < 0.3 and sc.get("first"): d.rectangle([0, 0, W, H], fill=(0, 0, 0, int(255 * (1 - t / 0.3))))
    return img, d

def s_date(sc, t, D, ctx):
    if sc.get("atmosphere") == "night_sea": img = night().copy()
    else: img = bgimg().copy()
    d = ImageDraw.Draw(img, "RGBA")
    atm = sc.get("atmosphere")
    if atm == "night_sea": sea(d, t)
    else: atmosphere(d, atm, t)
    p = eout(prog(t, 0.1, 0.6)); s = 1.0 + 0.04 * (t / D)
    y = 620 if atm == "night_sea" else 760
    big = sc["big"]; sz = fit_font(d, big, FBLACK, 120, 1000) * s
    d.text((W / 2, y + (1 - p) * 40), big, font=F(FBLACK, sz), fill=rgba(WHITE, p), anchor="mm", stroke_width=5, stroke_fill=(0, 0, 0, int(200 * p)))
    lw = 380 * eout(prog(t, 0.5, 1.0)); d.rectangle([W / 2 - lw, y + 85, W / 2 + lw, y + 91], fill=AMBER)
    if sc.get("sub"):
        f = F(FXB, fit_font(d, sc["sub"], FXB, 44, 1000)); typed(d, (W / 2 - d.textlength(sc["sub"], font=f) / 2, y + 150), sc["sub"], t, 0.9, 30, f, AMBER)
    overlay_stamp(img, sc, t, D, ctx, 1060)
    return img, d

def make_card(sc):
    cw, ch = 920, 560; im = shadowed(cw, ch, PAPER + (255,), radius=12); d = ImageDraw.Draw(im); ox, oy = 40, 40
    photo = sc.get("photo", True); tx = 300 if photo else 40
    for i in range(6): d.line([(ox + tx, oy + 150 + i * 66), (ox + cw - 30, oy + 150 + i * 66)], fill=(120, 150, 190, 90), width=2)
    d.line([(ox, oy + 80), (ox + cw, oy + 80)], fill=(180, 60, 50, 200), width=3)
    d.text((ox + 30, oy + 42), sc.get("header", "FILE"), font=F(FMONO, 28), fill=INK, anchor="lm")
    if photo:
        px, py = ox + 30, oy + 110
        d.rectangle([px, py, px + 240, py + 300], fill=(205, 196, 176))
        d.ellipse([px + 75, py + 55, px + 165, py + 150], fill=(150, 142, 126))
        d.rounded_rectangle([px + 35, py + 165, px + 205, py + 330], radius=60, fill=(150, 142, 126))
        d.rectangle([px - 4, py + 300, px + 244, py + 340], fill=PAPER)
        d.rectangle([px, py, px + 240, py + 300], outline=(150, 140, 120), width=3)
    return im, tx

def s_card(sc, t, D, ctx):
    img = bgimg().copy(); d0 = ImageDraw.Draw(img, "RGBA"); atmosphere(d0, sc.get("atmosphere"), t)
    if "card" not in ctx: ctx["card"] = make_card(sc)
    card, tx = ctx["card"]; p = eout(prog(t, 0, 0.5)); cx, cy = W / 2, 640 + (1 - p) * 1300
    paste(img, card, cx, cy); ox, oy = cx - card.width / 2 + 40, cy - card.height / 2 + 40
    d = ImageDraw.Draw(img, "RGBA"); lines = sc.get("lines", [])
    maxc = 25 if sc.get("photo", True) else 38
    f = F(FMONO, 30 if sc.get("photo", True) else 34)
    total = sum(len(l) for l in lines) or 1; t0 = 0.5; span = max(0.5, min(D * 0.55, total / 18))
    acc = 0
    for i, ln in enumerate(lines[:6]):
        ln = ln[:maxc]; st = t0 + span * acc / total; acc += len(ln)
        typed(d, (ox + tx, oy + 130 + i * 66), ln, t, st, max(len(ln) / (span * len(ln) / total), 8), f, INK)
    sk = sc.get("strike")
    if sk is not None:
        sp = eout(prog(t, sk["at"] * D, sk["at"] * D + 0.4)); i = sk["line"]
        if sp > 0 and i < len(lines):
            x0 = ox + tx - 4; x1 = x0 + d.textlength(lines[i][:maxc], font=f) + 8
            d.line([(x0, oy + 130 + i * 66), (x0 + (x1 - x0) * sp, oy + 130 + i * 66)], fill=INKRED, width=6)
    ad = sc.get("added")
    if ad:
        for j, ln in enumerate(ad["lines"][:2]):
            yy = oy + 130 + (len(lines) + j) * 66
            cps_a = max(22, len(ln) / max(0.4, D * (0.9 - ad["at"]) / len(ad["lines"][:2])))
            typed(d, (ox + tx, yy), ln[:maxc], t, ad["at"] * D + j * len(ln) / cps_a, cps_a, f, INKRED)
    overlay_stamp(img, sc, t, D, ctx, 1060)
    return img, d

def s_quote(sc, t, D, ctx):
    img = bgimg().copy()
    style = sc.get("style", "telegram")
    if "paper" not in ctx:
        w, h = 860, 640; fill = {"telegram": (236, 225, 180, 255), "newspaper": (232, 228, 216, 255), "letter": (246, 240, 226, 255)}[style]
        im = shadowed(w, h, fill, radius=6); d = ImageDraw.Draw(im); ox, oy = 40, 40
        if style == "telegram":
            d.rectangle([ox, oy, ox + w, oy + 90], fill=(200, 180, 120))
            d.text((ox + w / 2, oy + 45), sc.get("header", "T E L E G R A M"), font=F(FBLACK, 46), fill=(70, 55, 25), anchor="mm")
        elif style == "newspaper":
            d.text((ox + w / 2, oy + 60), sc.get("header", "THE DAILY NEWS"), font=F("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 60), fill=INK, anchor="mm")
            d.line([(ox + 30, oy + 110), (ox + w - 30, oy + 110)], fill=INK, width=4)
        else:
            d.text((ox + 40, oy + 60), sc.get("header", ""), font=F(FMONO, 30), fill=INK, anchor="lm")
        y = oy + 150
        for m in sc.get("meta", [])[:2]: d.text((ox + 40, y), m, font=F(FMONO, 30), fill=INK, anchor="lm"); y += 46
        d.line([(ox + 30, y), (ox + w - 30, y)], fill=(160, 140, 90), width=3)
        ctx["paper"] = (im, y + 70)
    paper, qy = ctx["paper"]; p = eout(prog(t, 0, 0.5)); cx, cy = W / 2, 760 + (1 - p) * 1300
    paste(img, paper, cx, cy); ox, oy = cx - paper.width / 2 + 40, cy - paper.height / 2 + 40
    d = ImageDraw.Draw(img, "RGBA")
    serif = style == "newspaper"
    f = F("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if serif else FMONO, 50)
    lines = wrap(sc["quote"], 22 if not serif else 24)[:4]
    start = sc.get("at", 0.15) * D; total = sum(len(l) for l in lines); cps = max(12, total / max(0.6, D * 0.5)); acc = 0
    for i, ln in enumerate(lines):
        n = int(clamp((t - start) * cps - acc, 0, len(ln))); acc += len(ln)
        if n > 0:
            full = d.textlength(ln, font=f); d.text((ox + 430 - full / 2, oy + qy - 40 + i * 80), ln[:n], font=f, fill=INK, anchor="lm")
    if sc.get("attribution"):
        a = prog(t, start + total / cps, start + total / cps + 0.4)
        d.text((ox + 820, oy + qy - 40 + len(lines) * 80 + 20), "— " + sc["attribution"], font=F(FMONO, 30), fill=rgba(INK, a), anchor="rm")
    overlay_stamp(img, sc, t, D, ctx, 1150)
    return img, d

def s_counter(sc, t, D, ctx):
    img = bgimg().copy(); d = ImageDraw.Draw(img, "RGBA"); atmosphere(d, sc.get("atmosphere"), t)
    if sc.get("header"): d.text((W / 2, 520), sc["header"], font=F(FXB, fit_font(d, sc["header"], FXB, 46, 1000)), fill=rgba(AMBER, eout(prog(t, 0, 0.4))), anchor="mm")
    a0 = sc.get("at", 0.08) * D; dur = min(sc.get("dur", 0.25) * D, 2.2); cp = prog(t, a0, a0 + dur)
    if cp > 0:
        v = sc["value"]; n = v * eout(cp)
        txt = sc.get("prefix", "") + (f"{int(round(n)):,}" if float(v).is_integer() else f"{n:,.1f}") + sc.get("suffix", "")
        pop = 1 + 0.08 * math.exp(-max(0, t - a0 - dur) * 6) if t > a0 + dur else 1
        sz = fit_font(d, sc.get("prefix", "") + f"{v:,}" + sc.get("suffix", ""), FBLACK, 190, 1000) * pop
        d.text((W / 2, 800), txt, font=F(FBLACK, sz), fill=col(sc.get("color", "w")), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0))
    if sc.get("sub"):
        d.text((W / 2, 960), sc["sub"], font=F(FXB, fit_font(d, sc["sub"], FXB, 52, 1000)), fill=rgba(WHITE, prog(t, a0 + dur, a0 + dur + 0.4)), anchor="mm")
    overlay_stamp(img, sc, t, D, ctx, 1100)
    return img, d

def s_timeline(sc, t, D, ctx):
    img = bgimg().copy(); d = ImageDraw.Draw(img, "RGBA")
    if sc.get("header"): d.text((W / 2, 260), sc["header"], font=F(FXB, fit_font(d, sc["header"], FXB, 46, 1000)), fill=AMBER, anchor="mm")
    ev = sc["events"][:5]; n = len(ev); top, bot = 380, 1080; step = (bot - top) / max(1, n - 1)
    lp = eout(prog(t, 0.1, D * 0.8)); d.line([(200, top), (200, top + (bot - top) * lp)], fill=(86, 110, 134), width=6)
    for i, e in enumerate(ev):
        y = top + i * step; a0 = 0.2 + (D * 0.75) * i / max(1, n); p = eout(prog(t, a0, a0 + 0.35))
        if p <= 0: continue
        cc = col(e.get("color", "a" if i == n - 1 else "w"))
        r = 16 * eback(prog(t, a0, a0 + 0.3)); d.ellipse([200 - r, y - r, 200 + r, y + r], fill=cc)
        d.text((250 + (1 - p) * 30, y - 22), e["date"], font=F(FBLACK, 54), fill=rgba(cc, p), anchor="lm")
        d.text((250 + (1 - p) * 30, y + 32), e["label"], font=F(FBOLD, fit_font(d, e["label"], FBOLD, 36, 780)), fill=rgba(WHITE, p * 0.9), anchor="lm")
    return img, d

def s_end(sc, t, D, ctx):
    img = Image.new("RGB", (W, H), (6, 9, 14)); d = ImageDraw.Draw(img, "RGBA")
    lines = sc["lines"]; y = 960 - (len(lines) - 1) * 75
    for i, ln in enumerate(lines):
        p = prog(t, 0.1 + i * 0.3, 0.4 + i * 0.3); sz = fit_font(d, ln["t"], FBLACK, 140, 1000)
        if p > 0: d.text((W / 2, y), ln["t"], font=F(FBLACK, sz * (1.25 - 0.25 * eout(p))), fill=rgba(col(ln.get("c", "w")), eout(p)), anchor="mm")
        y += 150
    d.text((W / 2, y + 60), sc.get("cta", "Follow for more true history"), font=F(FBOLD, 40), fill=rgba(GREY, prog(t, 1.3, 1.8)), anchor="mm")
    if t > D - 0.4: d.rectangle([0, 0, W, H], fill=(0, 0, 0, int(255 * prog(t, D - 0.4, D))))
    return img, d

SCENES = {"hook": s_hook, "map": s_map, "date": s_date, "card": s_card, "quote": s_quote,
          "counter": s_counter, "timeline": s_timeline, "end": s_end}

# ---------------- pipeline ----------------
def tts(spec, outdir):
    from kokoro_onnx import Kokoro
    import soundfile as sf
    mdir = os.path.join(HERE, "models")
    k = None; timing = []
    for si, sc in enumerate(spec["scenes"]):
        row = []
        for gi, seg in enumerate(sc["segments"]):
            import hashlib
            f = os.path.join(outdir, "vo_" + hashlib.md5(seg["vo"].encode()).hexdigest()[:12] + ".wav")
            if not os.path.exists(f):
                if k is None: k = Kokoro(os.path.join(mdir, "kokoro-v1.0.onnx"), os.path.join(mdir, "voices-v1.0.bin"))
                a, sr = k.create(seg["vo"], voice=VOICE, speed=SPEED, lang="en-gb"); sf.write(f, a, sr)
            info = sf.info(f); row.append((f, info.frames / info.samplerate))
        timing.append(row)
    return timing

def layout(spec, timing):
    plan, start = [], 0.0
    for sc, row in zip(spec["scenes"], timing):
        segs, cur = [], 0.0
        for seg, (f, L) in zip(sc["segments"], row):
            d = LEAD + L + GAP; segs.append((cur, cur + d, f, L, seg.get("cap", seg["vo"]))); cur += d
        cur += TAILS.get(sc["type"], 0.0) + sc.get("hold", 0.0)
        plan.append({"start": start, "dur": cur, "segs": segs}); start += cur
    return plan, start

def frame(sc, t, D, ctx, caps):
    img, d = SCENES[sc["type"]](sc, t, D, ctx)
    caption(d, caps, t)
    return img

def render_scene(args):
    i, sc, pl, outdir = args
    D = pl["dur"]; caps = [] if sc["type"] in ("hook", "end") else [(a, b, c) for a, b, _, _, c in pl["segs"]]
    tmp = os.path.join(outdir, f"scene{i:02d}.tmp.mp4"); fin = os.path.join(outdir, f"scene{i:02d}.mp4")
    pr = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-g", "60", tmp], stdin=subprocess.PIPE)
    ctx = {}
    for k in range(int(round(D * FPS))): pr.stdin.write(frame(sc, k / FPS, D, ctx, caps).tobytes())
    pr.stdin.close()
    if pr.wait() != 0: raise SystemExit(f"scene {i} failed")
    os.replace(tmp, fin); return fin

def preview(spec, plan, outdir):
    ims = []
    for sc, pl in zip(spec["scenes"], plan):
        D = pl["dur"]; caps = [] if sc["type"] in ("hook", "end") else [(a, b, c) for a, b, _, _, c in pl["segs"]]
        ims.append(frame(sc, D * 0.8, D, {}, caps).resize((270, 480)))
    sheet = Image.new("RGB", (270 * min(6, len(ims)), 480 * ((len(ims) + 5) // 6)))
    for k, im in enumerate(ims): sheet.paste(im, ((k % 6) * 270, (k // 6) * 480))
    sheet.save(os.path.join(outdir, "preview.jpg"), quality=85)

def soundtrack(spec, plan, total, outdir):
    import soundfile as sf
    from scipy.signal import butter, sosfilt
    SR = 48000; N = int(total * SR) + SR; rng = np.random.default_rng(1)
    lp = lambda x, f: sosfilt(butter(2, f, "low", fs=SR, output="sos"), x)
    hp = lambda x, f: sosfilt(butter(2, f, "high", fs=SR, output="sos"), x)
    bp = lambda x, a, b: sosfilt(butter(2, [a, b], "band", fs=SR, output="sos"), x)
    def env(n, att, rel):
        tt = np.arange(n) / SR; return np.minimum(1, tt / max(att, 1e-4)) * np.exp(-np.maximum(0, tt - att) / rel)
    def add(buf, x, t, g=1.0):
        i = int(max(0, t) * SR)
        if i < len(buf): x = x[:len(buf) - i]; buf[i:i + len(x)] += x * g
    def thud(s=1.0):
        n = int(.6 * SR); tt = np.arange(n) / SR
        return (np.sin(2 * np.pi * (70 * tt - 30 * tt ** 2)) * env(n, .003, .16) * .9 + lp(rng.standard_normal(n), 2500) * env(n, .001, .02) * .5) * s
    def stamp():
        n = int(.5 * SR); return thud(1.0)[:n] * .9 + bp(rng.standard_normal(n), 150, 1800) * env(n, .001, .05) * .9
    def whoosh(dd=.7):
        n = int(dd * SR); e = np.sin(np.pi * np.arange(n) / n) ** 2; return bp(rng.standard_normal(n), 300, 3500) * e * .35
    def click():
        n = int(.025 * SR); return hp(rng.standard_normal(n), 2000) * env(n, .0005, .006) * rng.uniform(.5, 1)
    def ping():
        n = int(1.4 * SR); tt = np.arange(n) / SR
        return (np.sin(2 * np.pi * 880 * tt) + .4 * np.sin(2 * np.pi * 1320 * tt)) * env(n, .005, .35) * .18
    def typing(buf, t0, chars, dur):
        for c in range(chars): add(buf, click(), t0 + dur * c / max(1, chars) + rng.uniform(-.01, .01), .55)
    fx = np.zeros(N)
    for sc, pl in zip(spec["scenes"], plan):
        s0, D, ty = pl["start"], pl["dur"], sc["type"]
        add(fx, whoosh(.6), s0 - .2, .7)
        if ty == "hook":
            n = len(sc["lines"])
            for i in range(n): add(fx, thud(1.2 if i == n - 1 else .5), s0 + .48 + i * .4)
        elif ty == "end":
            for i in range(len(sc["lines"])): add(fx, thud(1.3 if i == len(sc["lines"]) - 1 else .6), s0 + .4 + i * .3)
        elif ty == "map":
            for p in sc.get("pins", []): add(fx, ping(), s0 + p.get("at", .1) * D)
            for a in sc.get("arrows", []): add(fx, whoosh(a.get("dur", .25) * D + .2), s0 + a.get("at", .3) * D, .8)
            if sc.get("big"): add(fx, thud(1.0), s0 + sc["big"].get("at", .5) * D)
        elif ty == "card":
            tot = sum(len(l) for l in sc.get("lines", [])); typing(fx, s0 + .5, tot, max(.5, min(D * .55, tot / 18)))
            if sc.get("added"): typing(fx, s0 + sc["added"]["at"] * D, sum(len(l) for l in sc["added"]["lines"]), 1.2)
        elif ty == "quote":
            tot = len(sc["quote"]); cps = max(12, tot / max(.6, D * .5)); typing(fx, s0 + sc.get("at", .15) * D, tot, tot / cps)
        elif ty == "counter":
            a0 = sc.get("at", .08) * D; dd = min(sc.get("dur", .25) * D, 2.2)
            for k in range(14): add(fx, click() * 2, s0 + a0 + dd * k / 14, .6)
            add(fx, thud(1.1), s0 + a0 + dd)
        elif ty == "timeline":
            n = len(sc["events"][:5])
            for i in range(n): add(fx, ping() * .7, s0 + .2 + (D * .75) * i / max(1, n))
        elif ty == "date" and sc.get("atmosphere") == "night_sea":
            n = int(D * SR); tt = np.arange(n) / SR
            add(fx, lp(rng.standard_normal(n), 600) * (.55 + .45 * np.sin(2 * np.pi * .22 * tt)) * .45 * np.minimum(1, tt / .6), s0)
        if sc.get("stamp"):
            yy = {"map": .55}.get(ty, .55); add(fx, stamp(), s0 + sc["stamp"].get("at", yy) * D + .12, 1.2)
    tt = np.arange(N) / SR
    def pad(f, a, l): return a * np.sin(2 * np.pi * f * tt + .3 * np.sin(2 * np.pi * .07 * tt)) * (.6 + .4 * np.sin(2 * np.pi * l * tt))
    mus = pad(55, .22, .05) + pad(82.41, .14, .07) + pad(110, .10, .09) + pad(130.81, .06, .11) + pad(220.2, .025, .13) + pad(659.3, .012, .2) + pad(523.3, .01, .17)
    beat = np.zeros(N); kick = thud(.5)[:int(.3 * SR)]; bt = 60 / 84; k = 0.0
    while k < total - 2: add(beat, kick, k, .35 if int(k / bt) % 4 else .5); k += bt
    mus = lp(mus + beat, 3000) * np.minimum(1, tt / 1.5) * np.clip((total - tt) / 2.0, 0, 1)
    vo = np.zeros(N)
    for pl in plan:
        for a, b, f, L, _ in pl["segs"]:
            x, sr = sf.read(f); x = x.mean(1) if x.ndim > 1 else x
            add(vo, np.interp(np.arange(int(len(x) * SR / sr)) * sr / SR, np.arange(len(x)), x), pl["start"] + a + LEAD)
    vo = hp(vo, 70); e = lp(np.abs(vo), 4); e /= e.max() + 1e-9
    mix = (vo + mus * .32 * (1 - .55 * np.clip(e * 6, 0, 1)) + fx * .55)[:int(total * SR)]
    mix /= np.abs(mix).max() / .9
    out = os.path.join(outdir, "mix.wav"); sf.write(out, np.stack([mix, mix], 1), SR); return out

def make(spec_path, outdir):
    spec = json.load(open(spec_path)); outdir = os.path.abspath(outdir); os.makedirs(outdir, exist_ok=True)
    spec["scenes"][0]["first"] = True
    timing = tts(spec, outdir); plan, total = layout(spec, timing)
    print(f"planned length {total:.1f}s")
    preview(spec, plan, outdir)
    with Pool(2) as pool:
        files = pool.map(render_scene, [(i, sc, pl, outdir) for i, (sc, pl) in enumerate(zip(spec["scenes"], plan))])
    lst = os.path.join(outdir, "list.txt")
    with open(lst, "w") as fh:
        for f in files: fh.write(f"file '{f}'\n")
    vid = os.path.join(outdir, "video_only.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vid], check=True)
    mix = soundtrack(spec, plan, total, outdir)
    final = os.path.join(outdir, spec["slug"] + ".mp4"); tmp = final[:-4] + ".tmp.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", mix, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "160k", "-shortest",
                    "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, final)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", final], capture_output=True, text=True).stdout)
    print(f"DONE {final} {dur:.1f}s")

if __name__ == "__main__":
    make(sys.argv[1], sys.argv[2])
