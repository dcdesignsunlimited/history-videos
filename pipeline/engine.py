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
VOICE, SPEED = "bm_george", 1.3
LEAD, GAP = 0.03, 0.08
TAILS = {"hook": 0.3, "end": 1.1}
MIN_LEN, MAX_LEN, MAX_SCENE = 61.0, 72.0, 6.0   # TikTok monetization needs >60 s; keep every shot short
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANS = ["punch", "whip", "glitch", "zoom"]

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

def kinetic_chunks(segs):
    """Split each narration line into 1-3 word captions timed across its spoken span."""
    out = []
    for a, b, f, L, text in segs:
        if not text: continue
        words = text.split(); groups, cur = [], []
        for w in words:
            cur.append(w)
            if len(cur) >= 3 or w[-1] in ",.;:!?—" or (len(cur) == 2 and len(" ".join(cur)) > 14):
                groups.append(cur); cur = []
        if cur: groups.append(cur)
        s0, span = a + LEAD, max(L, 0.3)
        weights = [sum(len(w) for w in g) + 2 for g in groups]; tot = sum(weights); acc = 0
        for g, wgt in zip(groups, weights):
            st = s0 + span * acc / tot; acc += wgt; en = s0 + span * acc / tot
            out.append((st, en, g))
        if out: out[-1] = (out[-1][0], max(out[-1][1], b - 0.02), out[-1][2])
    return out

def caption(d, chunks, t):
    for (a, b, words) in chunks:
        if not (a <= t < b): continue
        f = F(FBLACK, 96)
        text = " ".join(words)
        sz = 96 if d.textlength(text, font=f) <= 960 else fit_font(d, text, FBLACK, 96, 960)
        f = F(FBLACK, sz * (1.18 - 0.18 * eout(prog(t, a, a + 0.09))))
        sp = d.textlength(" ", font=f); ws = [d.textlength(w, font=f) for w in words]
        x = W / 2 - (sum(ws) + sp * (len(ws) - 1)) / 2
        idx = int(clamp((t - a) / max(b - a, 0.01), 0, 0.9999) * len(words))
        for i, (w, ww) in enumerate(zip(words, ws)):
            d.text((x, 1320), w, font=f, fill=AMBER if i == idx and len(words) > 1 else WHITE, anchor="lm",
                   stroke_width=9, stroke_fill=(0, 0, 0))
            x += ww + sp

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
    img = glowbg(sc, t, 950, default="r"); d = ImageDraw.Draw(img, "RGBA"); atmosphere(d, sc.get("atmosphere"), t)
    if sc.get("tag"): label(d, (W / 2, 650), sc["tag"], 1.0, AMBER, 40)
    lines = sc["lines"]; y = 790; t0 = -0.3   # first line is already on screen at frame 0 (good auto-thumbnail)
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
        y += sz * 1.05 if i < len(lines) - 2 else sz * 1.12; t0 += 0.32
    return img, d

def s_date(sc, t, D, ctx):
    if sc.get("atmosphere") == "night_sea": img = night().copy()
    else: img = glowbg(sc, t, 800)
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
    img = glowbg(sc, t, 640); d0 = ImageDraw.Draw(img, "RGBA"); atmosphere(d0, sc.get("atmosphere"), t)
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
    img = glowbg(sc, t, 760)
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
    img = glowbg(sc, t, 820); d = ImageDraw.Draw(img, "RGBA"); atmosphere(d, sc.get("atmosphere"), t)
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
    img = glowbg(sc, t, 730); d = ImageDraw.Draw(img, "RGBA")
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

_DARK = None
def s_end(sc, t, D, ctx):
    global _DARK
    if _DARK is None: _DARK = Image.new("RGB", (W, H), (6, 9, 14))
    img = glowbg(sc, t, 820, base=_DARK); d = ImageDraw.Draw(img, "RGBA")
    lines = sc["lines"]; y = 820 - (len(lines) - 1) * 75
    for i, ln in enumerate(lines):
        p = prog(t, 0.1 + i * 0.3, 0.4 + i * 0.3); sz = fit_font(d, ln["t"], FBLACK, 140, 1000)
        if p > 0: d.text((W / 2, y), ln["t"], font=F(FBLACK, sz * (1.25 - 0.25 * eout(p))), fill=rgba(col(ln.get("c", "w")), eout(p)), anchor="mm")
        y += 150
    q = sc.get("question")
    if q:   # comment prompt: drives comments, which the algorithms reward
        qa = eout(prog(t, 0.2 + len(lines) * 0.3, 0.6 + len(lines) * 0.3))
        qy = y + 10
        qlines = [q] if d.textlength(q, font=F(FXB, 54)) <= 980 else wrap(q, 28)[:3]
        for ln in qlines:
            d.text((W / 2, qy), ln, font=F(FXB, 54), fill=rgba(AMBER, qa), anchor="mm"); qy += 70
        y = qy
    d.text((W / 2, y + 60), sc.get("cta", "Follow for a new true story every day"), font=F(FBOLD, 40), fill=rgba(GREY, prog(t, 1.3, 1.8)), anchor="mm")
    if t > D - 0.4: d.rectangle([0, 0, W, H], fill=(0, 0, 0, int(255 * prog(t, D - 0.4, D))))
    return img, d

# ---------------- icons (Tabler, MIT; fetched by setup.sh into pipeline/icons_src) ----------------
_ICON = {}
def icon_img(name, size, c=WHITE, outline=False):
    key = (name, int(size), tuple(c), outline)
    if key not in _ICON:
        import cairosvg, io
        base = os.path.join(HERE, "icons_src")
        f = os.path.join(base, "filled", name + ".svg")
        if outline or not os.path.exists(f): f = os.path.join(base, "outline", name + ".svg")
        if not os.path.exists(f): raise SystemExit(f"unknown icon '{name}' (ls pipeline/icons_src/outline)")
        hexc = "#%02x%02x%02x" % tuple(c[:3])
        svg = open(f).read().replace("currentColor", hexc).replace('stroke-width="2"', 'stroke-width="2.1"')
        png = cairosvg.svg2png(bytestring=svg.encode(), output_width=int(size), output_height=int(size))
        im = Image.open(io.BytesIO(png)).convert("RGBA")
        # soft dark halo so icons read on any background
        halo = Image.new("RGBA", (im.width + 40, im.height + 40), (0, 0, 0, 0)); halo.paste(im, (20, 20), im)
        a = halo.getchannel("A").filter(ImageFilter.GaussianBlur(9)).point(lambda v: min(255, int(v * 1.3)))
        out = Image.new("RGBA", halo.size, (0, 0, 0, 0)); out.putalpha(a.point(lambda v: int(v * 0.75)))
        out.alpha_composite(halo)
        _ICON[key] = out
    return _ICON[key]

def overlay_icons(img, sc, t, D):
    """Pop-in icons on any scene: "icons": [{"name","x","y","size","color","at","label"}], x/y are 0-1 of the frame."""
    d = ImageDraw.Draw(img, "RGBA")
    for k, ic in enumerate(sc.get("icons", [])):
        a0 = ic.get("at", 0.1) * D; p = prog(t, a0, a0 + 0.3)
        if p <= 0: continue
        size = ic.get("size", 200); cc = col(ic.get("color", "a"), AMBER)
        x, y = ic.get("x", 0.5) * W, ic.get("y", 0.5) * H + math.sin(t * 2.4 + k) * 8
        paste(img, icon_img(ic["name"], size, cc, ic.get("outline", False)), x, y, eback(p), clamp(p * 3))
        if ic.get("label"):
            label(d, (x, y + size * 0.62 + 30), ic["label"], prog(t, a0 + 0.15, a0 + 0.45), WHITE, ic.get("label_size", 40))

def header(d, text, y, a=1.0, c=AMBER):
    if text: d.text((W / 2, y), text, font=F(FXB, fit_font(d, text, FXB, 50, 1000)), fill=rgba(c, a), anchor="mm")

# ---------------- v2 scene types ----------------
def s_icon(sc, t, D, ctx):
    """Hero beat: one big icon + big word. Fast, punchy filler between heavier scenes."""
    img = glowbg(sc, t, 700, default=sc.get("color", "a")); d = ImageDraw.Draw(img, "RGBA"); atmosphere(d, sc.get("atmosphere"), t)
    header(d, sc.get("header"), 330, eout(prog(t, 0, 0.3)))
    cc = col(sc.get("color", "a"), AMBER); p = prog(t, 0.0, 0.35)
    paste(img, icon_img(sc["icon"], 440, cc, sc.get("outline", False)), W / 2, 690 + math.sin(t * 2) * 10, 0.4 + 0.6 * eback(p), clamp(p * 3))
    d = ImageDraw.Draw(img, "RGBA")
    if sc.get("text"):
        q = eout(prog(t, 0.2, 0.5)); sz = fit_font(d, sc["text"], FBLACK, 150, 1000)
        d.text((W / 2, 1030), sc["text"], font=F(FBLACK, sz * (1.25 - 0.25 * q)), fill=rgba(WHITE, q), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0, int(220 * q)))
    if sc.get("sub"):
        d.text((W / 2, 1140), sc["sub"], font=F(FXB, fit_font(d, sc["sub"], FXB, 50, 1000)), fill=rgba(cc, prog(t, 0.45, 0.8)), anchor="mm")
    overlay_stamp(img, sc, t, D, ctx, 760)
    return img, d

def s_units(sc, t, D, ctx):
    """Unit chart: N icons fill in one by one (e.g. 21 people). Optional highlight of the last k in red."""
    img = glowbg(sc, t, 760, default=sc.get("color", "r")); d = ImageDraw.Draw(img, "RGBA")
    header(d, sc.get("header"), 300, eout(prog(t, 0, 0.3)))
    n = int(sc["count"]); per = sc.get("per_row") or max(4, min(10, math.ceil(math.sqrt(n * 1.3))))
    rows = math.ceil(n / per); cell = min(980 / per, 640 / rows); sz = cell * 0.82
    top = 690 - rows * cell / 2 + cell / 2; cc = col(sc.get("color", "w")); hl = sc.get("highlight", {})
    a0, a1 = 0.15, max(0.6, D * sc.get("fill", 0.55))
    for i in range(n):
        r_, c_ = divmod(i, per); rowlen = min(per, n - r_ * per)
        x = W / 2 + (c_ - (rowlen - 1) / 2) * cell; y = top + r_ * cell
        st = a0 + (a1 - a0) * i / max(1, n); p = prog(t, st, st + 0.18)
        if p <= 0: continue
        ic = col(hl.get("color", "r"), RED) if hl and i >= n - hl.get("count", 0) else cc
        paste(img, icon_img(sc.get("icon", "man"), sz, ic), x, y, eback(p))
    d = ImageDraw.Draw(img, "RGBA")
    shown = int(n * clamp((t - a0) / (a1 - a0)))
    if sc.get("big", True):
        txt = sc.get("prefix", "") + f"{shown:,}" + sc.get("suffix", "")
        d.text((W / 2, top + rows * cell + 40), txt, font=F(FBLACK, fit_font(d, sc.get("prefix", "") + f"{n:,}" + sc.get("suffix", ""), FBLACK, 130, 1000)),
               fill=col(sc.get("big_color", sc.get("color", "w"))), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0))
    if sc.get("sub"):
        d.text((W / 2, top + rows * cell + 150), sc["sub"], font=F(FXB, fit_font(d, sc["sub"], FXB, 46, 1000)), fill=rgba(WHITE, prog(t, a1, a1 + 0.4)), anchor="mm")
    overlay_stamp(img, sc, t, D, ctx, 1150)
    return img, d

def s_compare(sc, t, D, ctx):
    """Bars that grow side by side to show scale: items [{label, value, icon, color, text}]."""
    img = glowbg(sc, t, 760); d = ImageDraw.Draw(img, "RGBA")
    header(d, sc.get("header"), 270, eout(prog(t, 0, 0.3)))
    items = sc["items"][:4]; n = len(items); vmax = max(i["value"] for i in items)
    base, hmax = 1110, 560; slot = 1000 / n; bw = min(220, slot * 0.62)
    d.line([(60, base), (W - 60, base)], fill=(140, 160, 180), width=4)
    for k, it in enumerate(items):
        cx = 40 + slot * (k + 0.5); a0 = 0.15 + k * min(0.5, D * 0.18); p = eout(prog(t, a0, a0 + 0.55))
        if p <= 0: continue
        h = hmax * it["value"] / vmax * p; cc = col(it.get("color", "a" if k == n - 1 else "w"))
        d.rounded_rectangle([cx - bw / 2, base - h, cx + bw / 2, base], radius=12, fill=rgba(cc, 0.9))
        if it.get("icon"): paste(img, icon_img(it["icon"], min(150, bw * 0.8), cc), cx, base - h - 95, 1.0, p)
        d = ImageDraw.Draw(img, "RGBA")
        vt = it.get("text", f"{it['value']:g}{sc.get('unit', '')}")
        if p > 0.6: d.text((cx, base - h + 50 if h > 120 else base - h - 200), vt, font=F(FBLACK, fit_font(d, vt, FBLACK, 60, bw + 40)), fill=(14, 22, 33) if h > 120 else WHITE, anchor="mm")
        d.text((cx, base + 50), it["label"], font=F(FXB, fit_font(d, it["label"], FXB, 36, slot - 20)), fill=rgba(WHITE, p), anchor="mm")
    overlay_stamp(img, sc, t, D, ctx, 1180)
    return img, d

def s_split(sc, t, D, ctx):
    """Two panels side by side (cause/effect, before/after, expectation/reality)."""
    img = glowbg(sc, t, 760); d = ImageDraw.Draw(img, "RGBA")
    header(d, sc.get("header"), 330, eout(prog(t, 0, 0.3)))
    for k, side in enumerate(("left", "right")):
        pn = sc[side]; a0 = 0.1 if k == 0 else sc.get("right_at", 0.45) * D; p = eout(prog(t, a0, a0 + 0.35))
        if p <= 0: continue
        cx = (318 if k == 0 else 762) + (1 - p) * (-500 if k == 0 else 500); cc = col(pn.get("color", "w" if k == 0 else "a"))
        d.rounded_rectangle([cx - 196, 470, cx + 196, 1060], radius=26, fill=(255, 255, 255, 18), outline=rgba(cc, 0.8), width=4)
        if pn.get("title"): d.text((cx, 530), pn["title"], font=F(FXB, fit_font(d, pn["title"], FXB, 40, 360)), fill=cc, anchor="mm")
        if pn.get("icon"): paste(img, icon_img(pn["icon"], 240, cc), cx, 740)
        d = ImageDraw.Draw(img, "RGBA")
        for j, ln in enumerate(wrap(pn.get("text", ""), 14)[:3]):
            d.text((cx, 940 + j * 52), ln, font=F(FBLACK, fit_font(d, ln, FBLACK, 48, 360)), fill=WHITE, anchor="mm")
    if sc.get("mid"):
        q = eback(prog(t, sc.get("right_at", 0.45) * D, sc.get("right_at", 0.45) * D + 0.3))
        if q > 0: d.ellipse([540 - 60 * q, 765 - 60 * q, 540 + 60 * q, 765 + 60 * q], fill=RED); d.text((540, 765), sc["mid"], font=F(FBLACK, 44 * q + 1), fill=WHITE, anchor="mm")
    overlay_stamp(img, sc, t, D, ctx, 1160)
    return img, d

_PHOTO = {}
def load_photo(path, grade):
    key = (path, grade)
    if key not in _PHOTO:
        full = path if os.path.isabs(path) else os.path.join(ROOT, path)
        if not os.path.exists(full): raise SystemExit(f"photo not found: {full}")
        im = Image.open(full).convert("RGB")
        if grade in ("bw", "sepia"):
            g = im.convert("L"); g = Image.eval(g, lambda v: int(clamp((v - 128) * 1.18 + 128, 0, 255)))
            im = Image.merge("RGB", (g, g, g)) if grade == "bw" else Image.merge("RGB", (g.point(lambda v: min(255, int(v * 1.07 + 10))), g.point(lambda v: int(v * 0.97 + 4)), g.point(lambda v: int(v * 0.82))))
        sc_ = max(W * 1.25 / im.width, H * 1.0 / im.height)   # cover the frame with room to pan
        im = im.resize((int(im.width * sc_) + 1, int(im.height * sc_) + 1), Image.LANCZOS)
        _PHOTO[key] = im
    return _PHOTO[key]

_VIG = None
def vignette():
    global _VIG
    if _VIG is None:
        y, x = np.mgrid[0:H, 0:W]; dd = np.sqrt(((x - W / 2) / (W * 0.62)) ** 2 + ((y - H / 2) / (H * 0.6)) ** 2)
        a = np.clip((dd - 0.55) * 1.6, 0, 0.85)
        a = np.maximum(a, np.clip((y - H * 0.6) / (H * 0.3), 0, 0.7))           # darken lower third for captions
        a = np.maximum(a, np.clip((H * 0.17 - y) / (H * 0.17), 0, 0.6))         # and the top for the header
        _VIG = Image.fromarray((a * 255).astype(np.uint8), "L")
    return _VIG

def s_photo(sc, t, D, ctx):
    """Archival photo with Ken Burns move, film grain, vignette, optional circle callout and credit."""
    im = load_photo(sc["image"], sc.get("grade", "sepia")); u = einout(t / D)
    pan = sc.get("pan", "in"); fx, fy = sc.get("focus", [0.5, 0.5])
    z = {"in": 1.0 + 0.22 * u, "out": 1.22 - 0.22 * u}.get(pan, 1.1)
    vw, vh = im.width / z, im.width / z * H / W
    if vh > im.height: vh = im.height; vw = vh * W / H
    cx, cy = fx * im.width, fy * im.height
    if pan == "left": cx = im.width * (0.62 - 0.24 * u)
    if pan == "right": cx = im.width * (0.38 + 0.24 * u)
    if pan == "up": cy = im.height * (0.62 - 0.24 * u)
    if pan == "down": cy = im.height * (0.38 + 0.24 * u)
    cx = clamp(cx, vw / 2, im.width - vw / 2); cy = clamp(cy, vh / 2, im.height - vh / 2)
    box = (cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2)
    img = im.resize((W, H), Image.BILINEAR, box=box)
    img.paste((0, 0, 0), (0, 0), vignette())
    rng = np.random.default_rng(int(t * 30))   # film grain
    g = rng.integers(-14, 15, (H // 4, W // 4), dtype=np.int16).repeat(4, 0).repeat(4, 1)[..., None]
    img = Image.fromarray(np.clip(np.asarray(img, np.int16) + g, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    if sc.get("header"): header(d, sc["header"], 230, eout(prog(t, 0.1, 0.4)))
    ci = sc.get("circle")
    if ci:   # hand-drawn ring around a point of the photo (coords are 0-1 of the photo)
        a0 = ci.get("at", 0.35) * D; p = eout(prog(t, a0, a0 + 0.45))
        if p > 0:
            px = (ci["x"] * im.width - box[0]) / vw * W; py = (ci["y"] * im.height - box[1]) / vh * H; r = ci.get("r", 120)
            d.arc([px - r, py - r * 0.85, px + r, py + r * 0.85], -100, -100 + 380 * p, fill=RED, width=10)
            if ci.get("label") and p > 0.9: label(d, (px, py + r + 40), ci["label"], prog(t, a0 + 0.4, a0 + 0.7), WHITE, 40)
    if sc.get("credit"): d.text((W - 130, 1640), sc["credit"], font=F(FBOLD, 26), fill=(220, 220, 220, 150), anchor="rm")
    overlay_stamp(img, sc, t, D, ctx, 1080)
    return img, d

# ---------------- camera, beats and transitions ----------------
FRAMES = [(0.0, 0, 0), (0.07, -28, -24), (0.035, 30, 18), (0.09, 0, -34)]

def glitch(img, t, amt):
    a = np.asarray(img).copy(); rng = np.random.default_rng(int(t * 1000) % 99991); s = int(28 * amt) + 2
    a[..., 0] = np.roll(a[..., 0], s, 1); a[..., 2] = np.roll(a[..., 2], -s, 1)
    for _ in range(int(9 * amt) + 1):
        y0 = rng.integers(0, H - 40); h = rng.integers(12, 140); a[y0:y0 + h] = np.roll(a[y0:y0 + h], int(rng.integers(-120, 120) * amt), 1)
    return Image.fromarray(a)

def whip(img, dx):
    a = np.asarray(img, np.float32); n = 7; L = int(abs(dx) * 0.35) + 1
    acc = sum(np.roll(a, int(dx) + int(L * k / n) * (1 if dx >= 0 else -1), 1) for k in range(n)) / n
    return Image.fromarray(acc.astype(np.uint8))

def zblur(img, amt):
    if amt < 0.05: return img
    sm = img.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(8 * amt))
    return Image.blend(img, sm.resize((W, H), Image.BILINEAR), clamp(amt))

def camera(img, s, ox, oy):
    if s <= 1.001 and not ox and not oy: return img
    s = max(s, 1.0); zw, zh = int(W * s), int(H * s); mx, my = (zw - W) / 2, (zh - H) / 2
    x0 = clamp(mx + ox * s, 0, zw - W); y0 = clamp(my + oy * s, 0, zh - H)
    return img.resize((W, H), Image.BILINEAR, box=(x0 / s, y0 / s, (x0 + W) / s, (y0 + H) / s))

SCENES = {"hook": s_hook, "map": s_map, "date": s_date, "card": s_card, "quote": s_quote,
          "counter": s_counter, "timeline": s_timeline, "end": s_end,
          "icon": s_icon, "units": s_units, "compare": s_compare, "split": s_split, "photo": s_photo}

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
            f = os.path.join(outdir, "vo_" + hashlib.md5(f"{VOICE}{SPEED}{seg['vo']}".encode()).hexdigest()[:12] + ".wav")
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

_GLOW = {}
def glow(c):
    if c not in _GLOW:
        g = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([150, 150, 750, 750], fill=c + (70,))
        _GLOW[c] = g.filter(ImageFilter.GaussianBlur(110))
    return _GLOW[c]

def glowbg(sc, t, y, base=None, default="a"):
    """Background with a soft pulsing color glow behind the main element, plus drifting dust."""
    img = (base or bgimg()).copy()
    gc = col(sc.get("color", default), AMBER)
    paste(img, glow(gc), W / 2, y, 1.0 + 0.06 * math.sin(t * 2.2), 0.75 + 0.25 * math.sin(t * 2.2))
    if not sc.get("atmosphere"): atmosphere(ImageDraw.Draw(img, "RGBA"), "dust", t)
    return img

def frame(sc, t, D, ctx, caps, gstart=0.0, total=1.0, beats=(), tin="punch", tout=None):
    img, _ = SCENES[sc["type"]](sc, t, D, ctx)
    overlay_icons(img, sc, t, D)
    # camera: steady push-in, plus a re-frame + punch on every new sentence ("beat") so the shot never sits still
    k = sum(1 for b in beats if t >= b)
    cur, prev = FRAMES[k % 4], FRAMES[(k - 1) % 4]
    e = eout(prog(t, beats[k - 1], beats[k - 1] + 0.28)) if k else 1.0
    z, ox, oy = (prev[i] + (cur[i] - prev[i]) * e for i in range(3))
    s = 1.0 + 0.08 * (t / D) + z + (0.05 * (1 - eout(prog(t, beats[k - 1], beats[k - 1] + 0.25))) if k else 0)
    sh = sc.get("shake")
    if sh:
        a0 = sh.get("at", 0) * D; amp = sh.get("amp", 26) * (1 - prog(t, a0, a0 + sh.get("dur", 0.5)))
        if t >= a0 and amp > 0: ox += math.sin(t * 97) * amp; oy += math.cos(t * 83) * amp; s += 0.03
    first = sc.get("first")
    if not first:   # entry transition
        if tin == "punch": s += 0.09 * (1 - eout(prog(t, 0, 0.3)))
        elif tin == "zoom": s += 0.45 * (1 - eout(prog(t, 0, 0.24)))
    if tout == "zoom": s += 0.35 * prog(t, D - 0.14, D) ** 2
    img = camera(img, s, ox, oy)
    if not first:
        if tin == "zoom" and t < 0.24: img = zblur(img, 1 - prog(t, 0, 0.24))
        elif tin == "whip" and t < 0.2: img = whip(img, W * 0.55 * (1 - eout(prog(t, 0, 0.2))))
        elif tin == "glitch" and t < 0.18: img = glitch(img, t, 1 - prog(t, 0, 0.18))
    if tout == "zoom" and t > D - 0.14: img = zblur(img, prog(t, D - 0.14, D))
    elif tout == "whip" and t > D - 0.12: img = whip(img, -W * 0.55 * prog(t, D - 0.12, D) ** 2)
    elif tout == "glitch" and t > D - 0.1: img = glitch(img, t, prog(t, D - 0.1, D))
    d = ImageDraw.Draw(img, "RGBA")
    if not first and tin == "punch" and t < 0.12:
        d.rectangle([0, 0, W, H], fill=(255, 255, 255, int(120 * (1 - t / 0.12))))
    caption(d, caps, t)
    gp = clamp((gstart + t) / total)
    d.rectangle([0, 0, W, 9], fill=(0, 0, 0, 90)); d.rectangle([0, 0, int(W * gp), 9], fill=AMBER)
    return img

def transitions(spec):
    out = []
    for i, sc in enumerate(spec["scenes"]):
        out.append(sc.get("trans", "zoom" if sc["type"] == "end" else TRANS[i % len(TRANS)]))
    return out

def beats_of(pl):
    return [a for a, *_ in pl["segs"][1:]]

def scene_caps(sc, pl):
    return [] if sc["type"] in ("hook", "end") else kinetic_chunks(pl["segs"])

def render_scene(args):
    i, sc, pl, outdir, total, tin, tout = args
    D = pl["dur"]; caps = scene_caps(sc, pl)
    tmp = os.path.join(outdir, f"scene{i:02d}.tmp.mp4"); fin = os.path.join(outdir, f"scene{i:02d}.mp4")
    pr = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-g", "60", tmp], stdin=subprocess.PIPE)
    ctx = {}
    bt = beats_of(pl)
    for k in range(int(round(D * FPS))): pr.stdin.write(frame(sc, k / FPS, D, ctx, caps, pl["start"], total, bt, tin, tout).tobytes())
    pr.stdin.close()
    if pr.wait() != 0: raise SystemExit(f"scene {i} failed")
    os.replace(tmp, fin); return fin

def preview(spec, plan, outdir, total):
    ims = []
    for sc, pl in zip(spec["scenes"], plan):
        D = pl["dur"]; caps = scene_caps(sc, pl)
        ims.append(frame(sc, D * 0.8, D, {}, caps, pl["start"], total, beats_of(pl)).resize((270, 480)))
    cols = 8
    sheet = Image.new("RGB", (270 * min(cols, len(ims)), 480 * ((len(ims) + cols - 1) // cols)))
    for k, im in enumerate(ims): sheet.paste(im, ((k % cols) * 270, (k // cols) * 480))
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
    def riser(dd=0.8):
        n = int(dd * SR); tt = np.arange(n) / SR; k = tt / dd
        tone = np.sin(2 * np.pi * (180 * tt + 600 * tt ** 2 / dd))
        return (tone * .25 + bp(rng.standard_normal(n), 800, 6000) * .5) * k ** 2.5 * .5
    def typing(buf, t0, chars, dur):
        for c in range(chars): add(buf, click(), t0 + dur * c / max(1, chars) + rng.uniform(-.01, .01), .55)
    fx = np.zeros(N); tr = transitions(spec)
    for si, (sc, pl) in enumerate(zip(spec["scenes"], plan)):
        s0, D, ty = pl["start"], pl["dur"], sc["type"]
        add(fx, whoosh(.6), s0 - .2, .7)
        if s0 > 0: add(fx, thud(.55), s0)   # impact on every cut
        if ty == "hook":
            n = len(sc["lines"])
            for i in range(1, n): add(fx, thud(1.2 if i == n - 1 else .5), s0 - .02 + .32 * i)
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
            add(fx, riser(dd), s0 + a0, .8); add(fx, thud(1.1), s0 + a0 + dd)
        elif ty == "timeline":
            n = len(sc["events"][:5])
            for i in range(n): add(fx, ping() * .7, s0 + .2 + (D * .75) * i / max(1, n))
        elif ty == "date" and sc.get("atmosphere") == "night_sea":
            n = int(D * SR); tt = np.arange(n) / SR
            add(fx, lp(rng.standard_normal(n), 600) * (.55 + .45 * np.sin(2 * np.pi * .22 * tt)) * .45 * np.minimum(1, tt / .6), s0)
        elif ty == "icon":
            add(fx, thud(1.0), s0 + .05)
        elif ty == "units":
            n = int(sc["count"]); a1 = max(.6, D * sc.get("fill", .55))
            for k in range(min(n, 40)): add(fx, click() * 2.5, s0 + .15 + (a1 - .15) * k / min(n, 40), .7)
            add(fx, thud(.9), s0 + a1)
        elif ty == "compare":
            for k in range(len(sc["items"][:4])):
                a0 = .15 + k * min(.5, D * .18); add(fx, riser(.5), s0 + a0, .5); add(fx, thud(.7 + .2 * k), s0 + a0 + .5)
        elif ty == "split":
            add(fx, whoosh(.4), s0 + .05, .7); ra = sc.get("right_at", .45) * D; add(fx, whoosh(.4), s0 + ra, .7)
            if sc.get("mid"): add(fx, thud(1.0), s0 + ra + .1)
        elif ty == "photo" and sc.get("circle"):
            add(fx, whoosh(.45), s0 + sc["circle"].get("at", .35) * D, .6)
        if sc.get("shake"): add(fx, thud(1.4), s0 + sc["shake"].get("at", 0) * D)
        for ic in sc.get("icons", []): add(fx, ping() * .6, s0 + ic.get("at", .1) * D)
        for a, *_ in pl["segs"][1:]: add(fx, whoosh(.3), s0 + a - .12, .35)   # soft swish on every beat
        if tr[si] == "glitch" and s0 > 0:
            n = int(.18 * SR); add(fx, (np.sign(rng.standard_normal(n)) * (rng.random(n) < .3)) * env(n, .001, .06) * .35, s0 - .05)
        if sc.get("stamp"):
            ts = s0 + sc["stamp"].get("at", .55) * D + .12
            add(fx, riser(.7), ts - .7, .7); add(fx, stamp(), ts, 1.2)
    tt = np.arange(N) / SR
    def pad(f, a, l): return a * np.sin(2 * np.pi * f * tt + .3 * np.sin(2 * np.pi * .07 * tt)) * (.6 + .4 * np.sin(2 * np.pi * l * tt))
    mus = pad(55, .22, .05) + pad(82.41, .14, .07) + pad(110, .10, .09) + pad(130.81, .06, .11) + pad(220.2, .025, .13) + pad(659.3, .012, .2) + pad(523.3, .01, .17)
    beat = np.zeros(N); kick = thud(.5)[:int(.3 * SR)]; bt = 60 / 104; k = 0.0
    hat = hp(rng.standard_normal(int(.04 * SR)), 6000) * env(int(.04 * SR), .001, .012)
    while k < total - 2:
        add(beat, kick, k, .4 if int(k / bt) % 4 else .55); add(beat, hat, k + bt / 2, .12); k += bt
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
    for i, (sc, pl) in enumerate(zip(spec["scenes"], plan)):
        if sc["type"] != "end" and pl["dur"] > MAX_SCENE + 1.0 * (len(pl["segs"]) - 1): print(f"WARNING scene {i} ({sc['type']}) is {pl['dur']:.1f}s - split it (max {MAX_SCENE:.0f}s)")
    if total < MIN_LEN or total > MAX_LEN:
        print(f"LENGTH FAIL {total:.1f}s - must be {MIN_LEN:.0f}-{MAX_LEN:.0f}s (target 62-70). Add or cut narration."); sys.exit(2)
    preview(spec, plan, outdir, total)
    tr = transitions(spec)
    with Pool(os.cpu_count() or 2) as pool:
        files = pool.map(render_scene, [(i, sc, pl, outdir, total, tr[i], tr[i + 1] if i + 1 < len(tr) else None)
                                        for i, (sc, pl) in enumerate(zip(spec["scenes"], plan))])
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
    if dur < MIN_LEN: print(f"LENGTH FAIL final video {dur:.1f}s < {MIN_LEN:.0f}s"); sys.exit(2)

if __name__ == "__main__":
    make(sys.argv[1], sys.argv[2])
