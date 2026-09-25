#!/usr/bin/env python3
"""Generate source/assets.c and include/assets.h: track path, sine table,
palettes, 256-color background images (title + level themes), sprite and
font tiles. Also writes build/preview_*.png for a quick look.

Run from the repo root:  python3 tools/gen_assets.py
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 240, 160
CX, CY = 120, 84           # frog position in game
TITLE_FROG = (120, 100)    # frog position on the title screen
BG_FIRST_COLOR = 32        # palette 0-31 is shared with the text layer
BG_MAX_COLORS = 256 - BG_FIRST_COLOR


def rgb15(r, g, b):
    return (int(r) >> 3) | ((int(g) >> 3) << 5) | ((int(b) >> 3) << 10)


def clamp(v, lo=0, hi=255):
    return lo if v < lo else hi if v > hi else v


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def scale(c, k):
    return tuple(clamp(v * k) for v in c)


# ---------------------------------------------------------------- noise
def hash2(ix, iy, seed):
    h = (ix * 374761393 + iy * 668265263 + seed * 982451653) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def vnoise(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = hash2(ix, iy, seed)
    b = hash2(ix + 1, iy, seed)
    c = hash2(ix, iy + 1, seed)
    d = hash2(ix + 1, iy + 1, seed)
    return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy


def fbm(x, y, seed, octaves=3):
    v, amp, tot = 0.0, 1.0, 0.0
    for o in range(octaves):
        v += vnoise(x, y, seed + o * 17) * amp
        tot += amp
        x, y, amp = x * 2.03, y * 2.03, amp * 0.5
    return v / tot


# ---------------------------------------------------------------- path
def build_path():
    theta_end = 3.5 * math.pi
    dense = [(256.0, CY)]
    x = 256.0
    while x > CX + 100.0:
        x -= 0.25
        dense.append((x, CY))
    steps = 20000
    for i in range(steps + 1):
        t = theta_end * i / steps
        f = t / theta_end
        rx = 100 - 56 * f
        ry = 68 - 38 * f
        dense.append((CX + rx * math.cos(t), CY - ry * math.sin(t)))
    pts = [dense[0]]
    acc = 0.0
    for (x0, y0), (x1, y1) in zip(dense, dense[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        while acc + seg >= 1.0:
            u = (1.0 - acc) / seg
            x0, y0 = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
            seg = math.hypot(x1 - x0, y1 - y0)
            acc = 0.0
            pts.append((x0, y0))
        acc += seg
    return [(int(round(x)), int(round(y))) for x, y in pts]


PATH = build_path()
END = PATH[-1]

# nearest path point for every pixel near the track
track_d = [[99.0] * W for _ in range(H)]
track_n = [[(0.0, 0.0)] * W for _ in range(H)]
for px, py in PATH:
    for y in range(py - 10, py + 11):
        if not 0 <= y < H:
            continue
        for x in range(px - 10, px + 11):
            if 0 <= x < W:
                d = math.hypot(x - px, y - py)
                if d < track_d[y][x]:
                    track_d[y][x] = d
                    track_n[y][x] = ((x - px) / d, (y - py) / d) if d > 0 else (0.0, 0.0)

LIGHT = (-0.6, -0.8)   # light from the top left


# ---------------------------------------------------------------- canvas helpers
class Canvas:
    def __init__(self, fill=(0, 0, 0)):
        self.px = [[fill] * W for _ in range(H)]

    def get(self, x, y):
        return self.px[y][x]

    def put(self, x, y, c, a=1.0):
        if 0 <= x < W and 0 <= y < H:
            if a >= 1.0:
                self.px[y][x] = c
            else:
                self.px[y][x] = mix(self.px[y][x], c, a)

    def shade(self, x, y, k):
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = scale(self.px[y][x], k)

    def each(self, fn, box=None):
        x0, y0, x1, y1 = box or (0, 0, W, H)
        for y in range(max(0, y0), min(H, y1)):
            for x in range(max(0, x0), min(W, x1)):
                r = fn(x, y, self.px[y][x])
                if r is not None:
                    self.px[y][x] = r


def in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def fill_poly(cv, poly, colfn):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    for y in range(int(min(ys)), int(max(ys)) + 1):
        for x in range(int(min(xs)), int(max(xs)) + 1):
            if in_poly(x + 0.5, y + 0.5, poly):
                c = colfn(x, y) if callable(colfn) else colfn
                cv.put(x, y, c)


def disc(cv, cx, cy, r, colfn):
    for y in range(int(cy - r - 1), int(cy + r + 2)):
        for x in range(int(cx - r - 1), int(cx + r + 2)):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d <= r:
                c = colfn(x, y, d) if callable(colfn) else colfn
                cv.put(x, y, c)


GOLD = (230, 176, 56)
GOLD_LT = (255, 232, 140)
GOLD_DK = (140, 88, 20)
JADE = (40, 160, 130)
JADE_LT = (120, 220, 180)
JADE_DK = (16, 80, 70)
TERRACOTTA = (180, 70, 40)
BONE = (236, 226, 196)


def greca_band(cv, y0, h, dark, fg, alt):
    """Interlocking stepped pyramids - a classic Aztec border."""
    for y in range(y0, y0 + h):
        for x in range(W):
            ly = (y - y0) * 8 // h
            if ly == 0 or y == y0 + h - 1:
                cv.put(x, y, dark)
                continue
            lx = x % 16
            s = 2 * int(abs(lx - 7.5) // 2)   # step height 0..6
            s = min(7, s + 1)
            if ly > s:
                c = fg
            elif ly < s:
                c = alt
            else:
                c = dark
            n = 0.9 + 0.2 * hash2(x, y, 5)
            cv.put(x, y, scale(c, n))


def sun_stone(cv, cx, cy, r, stone, dim=1.0):
    """Carved calendar-stone disc with rings, notches and rays."""
    def col(x, y, d):
        ang = math.atan2(y + 0.5 - cy, x + 0.5 - cx)
        a = (ang / (2 * math.pi)) % 1.0
        t = d / r
        n = 0.92 + 0.16 * fbm(x * 0.3, y * 0.3, 9, 2)
        base = scale(stone, n)
        if t > 0.94:
            c = scale(stone, 0.45)
        elif t > 0.78:
            seg = int(a * 40)
            c = scale(GOLD, n) if seg % 2 == 0 else scale(stone, n * 0.85)
            if abs(t - 0.86) < 0.02:
                c = scale(stone, 0.5)
        elif t > 0.74:
            c = scale(stone, 0.5)
        elif t > 0.42:
            # eight pointed rays
            k = (a * 8) % 1.0
            ray = abs(k - 0.5) * 2          # 0 at ray centre
            if ray < 1.0 - (t - 0.42) / 0.32:
                c = scale(GOLD, n * (1.05 - 0.3 * ray))
            else:
                c = scale(base, 0.8)
            if (a * 16) % 1.0 < 0.06:
                c = scale(stone, 0.55)
        elif t > 0.36:
            seg = int(a * 20)
            c = scale(JADE, n) if seg % 2 == 0 else scale(stone, 0.6)
        elif t > 0.32:
            c = scale(stone, 0.5)
        else:
            c = scale(base, 1.05)
        # simple top-left lighting on the rim
        if t > 0.9:
            nx, ny = (x + 0.5 - cx) / max(d, 0.1), (y + 0.5 - cy) / max(d, 0.1)
            c = scale(c, 1.0 + 0.5 * -(nx * LIGHT[0] + ny * LIGHT[1]) * -1)
        return scale(c, dim)
    disc(cv, cx, cy, r, col)


def pyramid(cv, x0, y1, w, steps, stone, flip=False):
    """Stepped temple pyramid with a stairway and a shrine on top."""
    sh = 4
    for s in range(steps):
        sw = w - s * (w // (steps + 1))
        sx = x0 + (w - sw) // 2
        sy = y1 - (s + 1) * sh
        for y in range(sy, sy + sh):
            for x in range(sx, sx + sw):
                n = 0.9 + 0.2 * hash2(x // 3, y // 2, 11)
                c = stone
                if y == sy:
                    c = scale(stone, 1.25)
                elif x == sx or x == sx + sw - 1:
                    c = scale(stone, 0.6)
                cv.put(x, y, scale(c, n))
    # stairway
    mid = x0 + w // 2
    top = y1 - steps * sh
    for y in range(top, y1):
        for x in range(mid - 2, mid + 2):
            c = scale(stone, 1.1 if (y - top) % 2 == 0 else 0.75)
            cv.put(x, y, c)
    # shrine
    sw = max(6, w // (steps + 1))
    for y in range(top - 6, top):
        for x in range(mid - sw // 2, mid + sw // 2):
            c = TERRACOTTA if y > top - 5 else GOLD
            if abs(x - mid) <= 1 and y > top - 4:
                c = (20, 12, 10)
            cv.put(x, y, c)


def serpent_head(cv):
    """Quetzalcoatl head the balls pour out of (right edge, facing left)."""
    def jade_shade(x, y):
        n = 0.8 + 0.35 * fbm(x * 0.4, y * 0.4, 21, 2)
        return scale(JADE, n)

    # feathered crest behind the head
    for i, c in enumerate([TERRACOTTA, GOLD, JADE_LT, TERRACOTTA]):
        for y in range(64 + i * 3, 106 - i * 3):
            cv.put(239 - i, y, c if (y + i) % 4 else scale(c, 0.7))
    upper = [(226, 80), (229, 74), (234, 69), (240, 67), (240, 80)]
    lower = [(227, 88), (240, 88), (240, 101), (235, 99), (230, 94)]
    fill_poly(cv, upper, jade_shade)
    fill_poly(cv, lower, jade_shade)
    for x in range(226, 240):
        cv.put(x, 80, JADE_DK)
        cv.put(x, 88, JADE_DK)
    for y in range(81, 88):
        for x in range(227, 240):
            cv.put(x, y, (40, 8, 12) if y not in (81, 87) else (70, 14, 20))
    for tx in range(228, 238, 3):     # fangs
        cv.put(tx, 81, BONE)
        cv.put(tx, 82, BONE)
        cv.put(tx + 1, 87, BONE)
        cv.put(tx + 1, 86, BONE)
    for (sx, sy) in [(233, 92), (237, 95), (236, 76), (232, 77)]:
        cv.put(sx, sy, JADE_LT)
        cv.put(sx + 1, sy + 1, JADE_DK)
    disc(cv, 235.5, 73.5, 2.4, GOLD)
    cv.put(235, 73, (20, 10, 10))
    cv.put(236, 73, (20, 10, 10))
    for x in range(231, 239):          # brow
        cv.put(x, 70 - (x - 231) // 4, JADE_DK)


def end_hole(cv, hx, hy):
    """Gold-rimmed pit at the end of the track."""
    def col(x, y, d):
        nx, ny = (x + 0.5 - hx) / max(d, 0.1), (y + 0.5 - hy) / max(d, 0.1)
        lit = nx * LIGHT[0] + ny * LIGHT[1]
        if d > 6.8:
            return GOLD_DK
        if d > 5.2:
            return scale(GOLD, 1.0 + 0.35 * lit)
        # inside the pit: dark, lit a little on the far wall
        return scale((40, 24, 16), max(0.15, 0.6 - 0.5 * lit) * (d / 5.2))
    disc(cv, hx, hy, 7.5, col)


def draw_track(cv, groove, lip):
    for y in range(H):
        for x in range(W):
            d = track_d[y][x]
            if d > 9.5:
                continue
            nx, ny = track_n[y][x]
            lit = nx * LIGHT[0] + ny * LIGHT[1]
            n = 0.9 + 0.2 * hash2(x, y, 3)
            if d <= 4.6:
                ao = 1.0 - 0.35 * (d / 4.6) ** 2
                cv.put(x, y, scale(groove, ao * n))
            elif d <= 5.8:
                cv.put(x, y, scale(lip, (0.8 - 0.45 * lit) * n))    # inner slope faces the centre
            elif d <= 7.0:
                cv.put(x, y, scale(lip, (0.85 + 0.45 * lit) * n))   # outer slope faces out
            elif lit < 0:
                cv.shade(x, y, 1.0 + 0.45 * lit * (9.5 - d) / 2.5)  # drop shadow


def hud_bar(cv):
    for y in range(0, 9):
        for x in range(W):
            n = 0.85 + 0.3 * fbm(x * 0.2, y * 0.2, 31, 2)
            cv.put(x, y, scale((52, 36, 28), n))
    for x in range(W):
        cv.put(x, 8, GOLD if (x // 4) % 2 == 0 else GOLD_DK)
        cv.put(x, 9, (20, 12, 8))


def corner_medallion(cv, cx, cy, stone):
    sun_stone(cv, cx, cy, 9, stone)


# ---------------------------------------------------------------- level themes
def floor_jungle(x, y):
    n = fbm(x / 22, y / 22, 1)
    m = fbm(x / 5, y / 5, 2, 2)
    c = mix((30, 78, 34), (70, 130, 52), n)
    c = scale(c, 0.85 + 0.3 * m)
    # leafy clumps
    if fbm(x / 9, y / 9, 3) > 0.66:
        c = mix(c, (24, 60, 22), 0.6) if hash2(x, y, 4) > 0.3 else (90, 160, 70)
    # little flowers
    h = hash2(x, y, 6)
    if h > 0.9965:
        c = (220, 60, 60)
    elif h > 0.9935:
        c = (240, 210, 70)
    return c


def floor_temple(x, y):
    bw, bh = 24, 14
    row = y // bh
    ox = (row % 2) * (bw // 2)
    bx, by = (x + ox) // bw, row
    lx, ly = (x + ox) % bw, y % bh
    tone = 0.85 + 0.25 * hash2(bx, by, 7)
    n = 0.9 + 0.2 * fbm(x / 4, y / 4, 8, 2)
    c = scale((200, 162, 110), tone * n)
    if lx == 0 or ly == 0:
        return scale(c, 0.55)
    if lx == 1 or ly == 1:
        return scale(c, 1.12)
    # some blocks carry a carved glyph
    if hash2(bx, by, 12) > 0.7:
        gx, gy = lx - bw // 2, ly - bh // 2
        if max(abs(gx), abs(gy)) in (4, 2) and abs(gx) <= 4 and abs(gy) <= 4:
            return scale(c, 0.7)
    # jade inlay at some corners
    if lx < 4 and ly < 4 and hash2(bx, by, 13) > 0.8 and lx + ly <= 3:
        return JADE if lx + ly < 3 else JADE_DK
    return c


def floor_night(x, y):
    bw, bh = 20, 20
    lx, ly = x % bw, y % bh
    bx, by = x // bw, y // bh
    tone = 0.8 + 0.3 * hash2(bx, by, 14)
    n = 0.85 + 0.3 * fbm(x / 5, y / 5, 15, 2)
    c = scale((54, 60, 82), tone * n)
    if fbm(x / 12, y / 12, 16) > 0.63:
        c = mix(c, (40, 80, 60), 0.5)   # moss
    if lx == 0 or ly == 0:
        return scale(c, 0.5)
    if lx == 1 or ly == 1:
        return scale(c, 1.15)
    return c


THEMES = [
    dict(name="jungle", floor=floor_jungle, groove=(112, 78, 44), lip=(160, 150, 126),
         stone=(160, 150, 130), glow=None),
    dict(name="temple", floor=floor_temple, groove=(100, 62, 36), lip=(186, 160, 118),
         stone=(176, 150, 112), glow=None),
    dict(name="night", floor=floor_night, groove=(34, 30, 40), lip=(110, 118, 140),
         stone=(118, 122, 138), glow=(255, 150, 50)),
]


def render_theme(t):
    cv = Canvas()
    cv.each(lambda x, y, c: t["floor"](x, y))
    # scenery in the corners, outside the spiral
    pyramid(cv, 2, 159, 28, 4, t["stone"])
    pyramid(cv, 210, 159, 28, 4, t["stone"])
    corner_medallion(cv, 14, 22, t["stone"])
    corner_medallion(cv, 226, 24, t["stone"])
    draw_track(cv, t["groove"], t["lip"])
    sun_stone(cv, CX, CY, 20, t["stone"])
    end_hole(cv, END[0], END[1])
    serpent_head(cv)
    if t["glow"]:
        glows = [(16, 128), (224, 128), (14, 22), (226, 24), (120, 84)]
        def light(x, y, c):
            k = 0.55
            add = [0.0, 0.0, 0.0]
            for gx, gy in glows:
                d = math.hypot(x - gx, y - gy)
                g = max(0.0, 1 - d / 60) ** 2
                k += 0.9 * g
                for i in range(3):
                    add[i] += t["glow"][i] * g * 0.25
            return tuple(clamp(c[i] * k + add[i]) for i in range(3))
        cv.each(light)
        # torch flames on the pyramid shrines
        for fx in (16, 224):
            for i, col in enumerate([(255, 240, 150), (255, 170, 40), (220, 70, 20)]):
                disc(cv, fx, 136 - i, 2.2 - i * 0.5 + 1, col) if i == 2 else None
            disc(cv, fx, 135, 2.2, (255, 170, 40))
            disc(cv, fx, 135.5, 1.2, (255, 240, 150))
    hud_bar(cv)
    return cv


# ---------------------------------------------------------------- title screen
LOGO = {
    "Z": ["#######", "#######", "....##.", "...##..", "..##...", ".##....", "#######", "#######"],
    "U": ["##...##", "##...##", "##...##", "##...##", "##...##", "##...##", "#######", ".#####."],
    "M": ["##...##", "###.###", "#######", "##.#.##", "##...##", "##...##", "##...##", "##...##"],
    "A": ["..###..", ".#####.", "##...##", "##...##", "#######", "#######", "##...##", "##...##"],
}


def glyph_mask(text, cell, gap, glyphs):
    cols = []
    for i, ch in enumerate(text):
        g = glyphs[ch]
        for gx in range(len(g[0])):
            cols.append([g[gy][gx] == "#" for gy in range(len(g))])
        if i < len(text) - 1:
            cols += [[False] * len(g)] * gap
    w = len(cols) * cell
    h = len(cols[0]) * cell
    return w, h, lambda x, y: 0 <= x < w and 0 <= y < h and cols[x // cell][y // cell]


def carved_text(cv, text, glyphs, cell, gap, top, face_top, face_bot, edge_lt, edge_dk, outline,
                band=True):
    w, h, m = glyph_mask(text, cell, gap, glyphs)
    x0 = (W - w) // 2
    # drop shadow
    for y in range(h):
        for x in range(w):
            if m(x, y):
                for o in (3, 4):
                    cv.shade(x0 + x + o, top + y + o, 0.45)
    # outline
    for y in range(-2, h + 2):
        for x in range(-2, w + 2):
            if not m(x, y) and any(m(x + dx, y + dy) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)):
                cv.put(x0 + x, top + y, outline)
    # face with bevel
    for y in range(h):
        for x in range(w):
            if not m(x, y):
                continue
            t = y / h
            c = mix(face_top, face_bot, t)
            n = 0.94 + 0.12 * fbm((x0 + x) * 0.25, (top + y) * 0.25, 41, 2)
            c = scale(c, n)
            if not m(x - 1, y) or not m(x, y - 1) or not m(x - 2, y - 2):
                c = edge_lt
            elif not m(x + 1, y) or not m(x, y + 1) or not m(x + 2, y + 2):
                c = edge_dk
            elif band and cell * 3 <= y < cell * 3 + 2:
                c = scale(c, 0.7)       # carved stripe
            elif band and y == cell * 3 + 2:
                c = scale(c, 1.15)
            cv.put(x0 + x, top + y, c)


SMALL_GLYPHS = {
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".####"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
}


def render_title():
    cv = Canvas()
    # temple wall
    def wall(x, y):
        bw, bh = 20, 10
        row = y // bh
        ox = (row % 2) * (bw // 2)
        bx = (x + ox) // bw
        lx, ly = (x + ox) % bw, y % bh
        tone = 0.8 + 0.3 * hash2(bx, row, 51)
        n = 0.85 + 0.3 * fbm(x / 4, y / 4, 52, 2)
        c = scale((150, 116, 80), tone * n)
        if lx == 0 or ly == 0:
            c = scale(c, 0.5)
        elif lx == 1 or ly == 1:
            c = scale(c, 1.1)
        # vignette
        vx, vy = (x - 120) / 140, (y - 70) / 110
        v = max(0.25, 1 - (vx * vx + vy * vy) * 1.3)
        return scale(c, v)
    cv.each(lambda x, y, c: wall(x, y))

    # hanging vines
    for vx in (6, 16, 30, 206, 222, 233):
        length = 30 + int(hash2(vx, 0, 55) * 60)
        for y in range(0, length):
            x = vx + int(2.5 * math.sin(y / 7 + vx))
            cv.put(x, y, (30, 90, 30))
            cv.put(x + 1, y, (60, 140, 50))
            if y % 6 == 0:
                side = 1 if (y // 6) % 2 else -1
                for k in range(1, 4):
                    cv.put(x + side * k, y + k // 2, (70, 160, 60))
                    cv.put(x + side * k, y + k // 2 + 1, (30, 100, 36))

    sun_stone(cv, 120, 40, 62, (176, 150, 112), dim=0.75)

    carved_text(cv, "ZUMA", LOGO, 6, 1, 14,
                face_top=(255, 222, 110), face_bot=(214, 120, 30),
                edge_lt=(255, 248, 200), edge_dk=(120, 60, 10), outline=(40, 16, 6))
    carved_text(cv, "GBA", SMALL_GLYPHS, 2, 1, 67,
                face_top=JADE_LT, face_bot=JADE, edge_lt=(210, 255, 230), edge_dk=JADE_DK,
                outline=(20, 12, 6), band=False)

    # stone pedestal for the frog
    sun_stone(cv, TITLE_FROG[0], TITLE_FROG[1], 18, (160, 150, 130))

    greca_band(cv, 146, 14, (30, 18, 10), GOLD, TERRACOTTA)
    for x in range(W):
        cv.put(x, 145, GOLD_DK)
    return cv


# ---------------------------------------------------------------- quantize
def quantize(cv, max_colors):
    """Median-cut the image down to max_colors. Returns (palette, index rows)."""
    counts = {}
    for row in cv.px:
        for c in row:
            k = rgb15(*c)
            counts[k] = counts.get(k, 0) + 1

    def comps(k):
        return (k & 31, (k >> 5) & 31, (k >> 10) & 31)

    boxes = [list(counts)]
    while len(boxes) < max_colors:
        best, best_score, best_ch = None, -1, 0
        for i, b in enumerate(boxes):
            if len(b) < 2:
                continue
            for ch in range(3):
                vals = [comps(k)[ch] for k in b]
                rng = max(vals) - min(vals)
                score = rng * sum(counts[k] for k in b)
                if score > best_score:
                    best, best_score, best_ch = i, score, ch
        if best is None:
            break
        b = sorted(boxes[best], key=lambda k: comps(k)[best_ch])
        tot = sum(counts[k] for k in b)
        acc, cut = 0, 1
        for j, k in enumerate(b):
            acc += counts[k]
            if acc >= tot / 2:
                cut = max(1, min(len(b) - 1, j))
                break
        boxes[best:best + 1] = [b[:cut], b[cut:]]

    pal, lut = [], {}
    for b in boxes:
        tot = sum(counts[k] for k in b)
        avg = [round(sum(comps(k)[ch] * counts[k] for k in b) / tot) for ch in range(3)]
        idx = len(pal)
        pal.append(avg[0] | (avg[1] << 5) | (avg[2] << 10))
        for k in b:
            lut[k] = idx
    rows = [[lut[rgb15(*c)] for c in row] for row in cv.px]
    return pal, rows


def to_tiles(img, w, h, bpp=4):
    """Tiles in row-major order, returned as u32 words."""
    words = []
    per = 32 // bpp
    for ty in range(h // 8):
        for tx in range(w // 8):
            for r in range(8):
                for half in range(8 // per):
                    v = 0
                    for c in range(per):
                        v |= (img[ty * 8 + r][tx * 8 + half * per + c] & ((1 << bpp) - 1)) << (bpp * c)
                    words.append(v)
    return words


def bg_image(cv):
    pal, rows = quantize(cv, BG_MAX_COLORS)
    full = [0] * 256
    for i, c in enumerate(pal):
        full[BG_FIRST_COLOR + i] = c
    idx = [[BG_FIRST_COLOR + v for v in row] for row in rows]
    return full, to_tiles(idx, W, H, 8), idx


def pal_to_rgb(pal):
    return [((c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3) for c in pal]


title_cv = render_title()
title_pal, title_tiles, title_idx = bg_image(title_cv)
theme_imgs = [bg_image(render_theme(t)) for t in THEMES]

# ---------------------------------------------------------------- sprite palettes
BALL_COLORS = [
    [(120, 10, 10), (220, 40, 40), (255, 120, 110), (255, 230, 230)],   # red
    [(10, 90, 20), (40, 190, 60), (140, 240, 130), (230, 255, 230)],    # green
    [(10, 30, 120), (40, 90, 230), (120, 170, 255), (230, 240, 255)],   # blue
    [(130, 100, 0), (240, 200, 20), (255, 240, 120), (255, 255, 230)],  # yellow
    [(80, 10, 110), (170, 50, 210), (220, 140, 250), (250, 230, 255)],  # purple
]
FROG_PAL = [(0, 0, 0), (20, 70, 20), (50, 160, 50), (130, 220, 100),
            (230, 220, 120), (255, 255, 255), (10, 10, 10), (170, 30, 40)]

# text layer palette (BG bank 1)
FONT_PAL = [(0, 0, 0), (255, 255, 255), (24, 14, 10), (58, 38, 28), (230, 176, 56),
            (140, 88, 20), (255, 226, 90), (84, 58, 40)]


def pal16(cols):
    cols = list(cols) + [(0, 0, 0)] * (16 - len(cols))
    return [rgb15(*c) for c in cols]


font_pal = pal16(FONT_PAL)
obj_pal = []
for c in BALL_COLORS:
    obj_pal += pal16([(0, 0, 0)] + c)
obj_pal += pal16(FROG_PAL)

# ---------------------------------------------------------------- sprites
ball = [[0] * 8 for _ in range(8)]
for y in range(8):
    for x in range(8):
        dx, dy = x + 0.5 - 4, y + 0.5 - 4
        d = math.hypot(dx, dy)
        if d > 4.0:
            continue
        hl = math.hypot(dx + 1.4, dy + 1.4)
        if hl < 1.0:
            ball[y][x] = 4
        elif d > 3.2 or dx + dy > 2.5:
            ball[y][x] = 1
        elif hl < 2.6:
            ball[y][x] = 3
        else:
            ball[y][x] = 2

frog = [[0] * 32 for _ in range(32)]


def idisc(img, cx, cy, r, col):
    for y in range(len(img)):
        for x in range(len(img[0])):
            if math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= r:
                img[y][x] = col


idisc(frog, 16, 17, 12.5, 1)
idisc(frog, 16, 17, 11.5, 2)
idisc(frog, 16, 19, 7, 4)
idisc(frog, 16, 26, 3.5, 1)      # socket for the next ball
idisc(frog, 9.5, 9, 4, 1)
idisc(frog, 22.5, 9, 4, 1)
idisc(frog, 9.5, 9, 3, 5)
idisc(frog, 22.5, 9, 3, 5)
idisc(frog, 9.5, 8.5, 1.3, 6)
idisc(frog, 22.5, 8.5, 1.3, 6)
idisc(frog, 16, 6, 4.5, 7)       # mouth
idisc(frog, 12, 14, 1.2, 3)
idisc(frog, 20, 14, 1.2, 3)

obj_tiles = to_tiles(ball, 8, 8)
obj_tiles += [0] * (8 * 3)      # pad so the frog starts at tile 4
obj_tiles += to_tiles(frog, 32, 32)
FROG_TILE = 4

# ---------------------------------------------------------------- font
FONT_CHARS = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ:!->"
GLYPHS = {
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "##### ...#. ..#.. ...#. ....# #...# .###.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ...#. .##..",
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    ":": "..... ..#.. ..#.. ..... ..#.. ..#.. .....",
    "!": "..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..",
    "-": "..... ..... ..... .###. ..... ..... .....",
    ">": "#.... .#... ..#.. ...#. ..#.. .#... #....",
}
# three styles: plain (transparent), on a panel, highlighted on a panel
FONT_STYLES = [(1, 2, 0), (1, 2, 3), (6, 2, 3)]
font_tiles = []
for fg, sh, bgc in FONT_STYLES:
    for ch in FONT_CHARS:
        img = [[bgc] * 8 for _ in range(8)]
        rows = GLYPHS.get(ch, ". " * 7).split()
        for r, row in enumerate(rows):
            for c, p in enumerate(row):
                if p == "#":
                    if img[r + 1][c + 2] == bgc:
                        img[r + 1][c + 2] = sh
                    img[r][c + 1] = fg
        font_tiles += to_tiles(img, 8, 8)


# panel frame: TL, T, TR, L, R, BL, B, BR
def frame_tile(top, bottom, left, right):
    img = [[3] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            if (top and y == 0) or (bottom and y == 7) or (left and x == 0) or (right and x == 7):
                img[y][x] = 5
            elif (top and y == 1) or (bottom and y == 6) or (left and x == 1) or (right and x == 6):
                img[y][x] = 4
            elif (top and y == 2) or (left and x == 2):
                img[y][x] = 7
    return to_tiles(img, 8, 8)


for spec in [(1, 0, 1, 0), (1, 0, 0, 0), (1, 0, 0, 1), (0, 0, 1, 0),
             (0, 0, 0, 1), (0, 1, 1, 0), (0, 1, 0, 0), (0, 1, 0, 1)]:
    font_tiles += frame_tile(*spec)

sin_tab = [int(round(math.sin(2 * math.pi * i / 256) * 4096)) for i in range(256)]


# ---------------------------------------------------------------- emit C
def c_array(ctype, name, vals, per_line=12, fmt="{}"):
    lines = []
    for i in range(0, len(vals), per_line):
        lines.append("    " + ", ".join(fmt.format(v) for v in vals[i:i + per_line]) + ",")
    return "const %s %s[%d] = {\n%s\n};\n" % (ctype, name, len(vals), "\n".join(lines))


def c_array2(ctype, name, rows, per_line=8, fmt="{}"):
    out = "const %s %s[%d][%d] = {\n" % (ctype, name, len(rows), len(rows[0]))
    for r in rows:
        out += "  {\n"
        for i in range(0, len(r), per_line):
            out += "    " + ", ".join(fmt.format(v) for v in r[i:i + per_line]) + ",\n"
        out += "  },\n"
    return out + "};\n"


NT = len(THEMES)
IMG_WORDS = len(title_tiles)
hdr = f"""// Generated by tools/gen_assets.py - do not edit.
#ifndef ASSETS_H
#define ASSETS_H

#include <stdint.h>

#define PATH_LEN {len(PATH)}
#define FROG_X {CX}
#define FROG_Y {CY}
#define TITLE_FROG_X {TITLE_FROG[0]}
#define TITLE_FROG_Y {TITLE_FROG[1]}
#define FROG_TILE {FROG_TILE}
#define NUM_COLORS {len(BALL_COLORS)}
#define FROG_PALBANK {len(BALL_COLORS)}
#define FONT_CHARS "{FONT_CHARS}"
#define FONT_NCHARS {len(FONT_CHARS)}
#define FRAME_TILE {len(FONT_CHARS) * len(FONT_STYLES)}
#define NUM_THEMES {NT}
#define BG_FIRST_COLOR {BG_FIRST_COLOR}
#define BG_IMG_WORDS {IMG_WORDS}

extern const int16_t path_x[PATH_LEN];
extern const int16_t path_y[PATH_LEN];
extern const int16_t sin_tab[256];
extern const uint16_t font_pal[16];
extern const uint16_t obj_pal[{len(obj_pal)}];
extern const uint16_t title_pal[256];
extern const uint32_t title_tiles[BG_IMG_WORDS];
extern const uint16_t theme_pal[NUM_THEMES][256];
extern const uint32_t theme_tiles[NUM_THEMES][BG_IMG_WORDS];
extern const uint32_t obj_tiles[{len(obj_tiles)}];
extern const uint32_t font_tiles[{len(font_tiles)}];

#endif
"""

src = "// Generated by tools/gen_assets.py - do not edit.\n#include \"assets.h\"\n\n"
src += c_array("int16_t", "path_x", [p[0] for p in PATH], 16)
src += c_array("int16_t", "path_y", [p[1] for p in PATH], 16)
src += c_array("int16_t", "sin_tab", sin_tab, 16)
src += c_array("uint16_t", "font_pal", font_pal, 8, "0x{:04X}")
src += c_array("uint16_t", "obj_pal", obj_pal, 8, "0x{:04X}")
src += c_array("uint16_t", "title_pal", title_pal, 8, "0x{:04X}")
src += c_array("uint32_t", "title_tiles", title_tiles, 8, "0x{:08X}")
src += c_array2("uint16_t", "theme_pal", [t[0] for t in theme_imgs], 8, "0x{:04X}")
src += c_array2("uint32_t", "theme_tiles", [t[1] for t in theme_imgs], 8, "0x{:08X}")
src += c_array("uint32_t", "obj_tiles", obj_tiles, 8, "0x{:08X}")
src += c_array("uint32_t", "font_tiles", font_tiles, 8, "0x{:08X}")

with open(os.path.join(ROOT, "include", "assets.h"), "w") as f:
    f.write(hdr)
with open(os.path.join(ROOT, "source", "assets.c"), "w") as f:
    f.write(src)


# ---------------------------------------------------------------- previews
def write_png(path, rows):
    raw = b"".join(b"\x00" + bytes(int(v) for px in row for v in px) for row in rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw)))
        f.write(chunk(b"IEND", b""))


def preview(name, pal, idx, frog_at, balls, text=()):
    rgb = pal_to_rgb(pal)
    img = [[rgb[idx[y][x]] for x in range(W)] for y in range(H)]

    def blit(spr, sx, sy, p):
        for y, row in enumerate(spr):
            for x, v in enumerate(row):
                if v and 0 <= sx + x < W and 0 <= sy + y < H:
                    img[sy + y][sx + x] = p[v]

    blit(frog, frog_at[0] - 16, frog_at[1] - 16, FROG_PAL)
    for i in range(balls):
        px, py = PATH[40 + i * 8]
        blit(ball, px - 4, py - 4, [(0, 0, 0)] + BALL_COLORS[(i * 7 // 3) % 5])
    for tx, ty, s in text:
        for i, ch in enumerate(s):
            rows = GLYPHS.get(ch, ". " * 7).split()
            for r, row in enumerate(rows):
                for c, p in enumerate(row):
                    if p == "#":
                        img[ty * 8 + r + 1][(tx + i) * 8 + c + 2] = FONT_PAL[2]
                        img[ty * 8 + r][(tx + i) * 8 + c + 1] = FONT_PAL[1]
    k = 3
    big = [[img[y // k][x // k] for x in range(W * k)] for y in range(H * k)]
    write_png(os.path.join(ROOT, "build", f"preview_{name}.png"), big)


os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
preview("title", title_pal, title_idx, TITLE_FROG, 0, [(9, 15, "PRESS START")])
for t, (pal, _, idx) in zip(THEMES, theme_imgs):
    preview(t["name"], pal, idx, (CX, CY), 30, [(0, 0, "SCORE 000120"), (21, 0, "LEVEL 01")])
print(f"path length {len(PATH)} px, end {END}")
