#!/usr/bin/env python3
"""Generate all of Zoomer DS's artwork and data:

  data/*.bin            LZ77-compressed 256x192 RGB15 pictures: title screens,
                        the top-screen dashboards and every level (layout x theme)
  source/assets.c       track layouts, 3D textures, top-screen sprites, font
  include/assets.h
  icon.bmp              the DS menu icon

Run from the repo root:  python3 tools/gen_assets.py
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 256, 192


def rgb15(r, g, b):
    return (int(r) >> 3) | ((int(g) >> 3) << 5) | ((int(b) >> 3) << 10)


def clamp(v, lo=0, hi=255):
    return lo if v < lo else hi if v > hi else v


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def scale(c, k):
    return tuple(clamp(v * k) for v in c)


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


LIGHT = (-0.6, -0.8)


def resample(dense):
    """Resample a dense polyline at 1px arc length, rounded to pixels."""
    pts = [dense[0]]
    acc = 0.0
    for (x0, y0), (x1, y1) in zip(dense, dense[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        while seg > 0 and acc + seg >= 1.0:
            u = (1.0 - acc) / seg
            x0, y0 = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
            seg = math.hypot(x1 - x0, y1 - y0)
            acc = 0.0
            pts.append((x0, y0))
        acc += seg
    return [(int(round(x)), int(round(y))) for x, y in pts]


def line(a, b):
    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) * 4))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def spiral(cx, cy, rx0, rx1, ry0, ry1, t0, turns, sign, steps=20000):
    pts = []
    for i in range(steps + 1):
        f = i / steps
        t = t0 + sign * turns * 2 * math.pi * f
        rx = rx0 + (rx1 - rx0) * f
        ry = ry0 + (ry1 - ry0) * f
        pts.append((cx + rx * math.cos(t), cy - ry * math.sin(t)))
    return pts


def rounded(poly, r):
    """Polyline with each corner rounded by a quadratic curve of radius ~r."""
    out = [poly[0]]
    for i in range(1, len(poly) - 1):
        p0, p1, p2 = poly[i - 1], poly[i], poly[i + 1]
        l1 = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        l2 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        rr = min(r, l1 / 2, l2 / 2)
        a = (p1[0] - (p1[0] - p0[0]) / l1 * rr, p1[1] - (p1[1] - p0[1]) / l1 * rr)
        b = (p1[0] + (p2[0] - p1[0]) / l2 * rr, p1[1] + (p2[1] - p1[1]) / l2 * rr)
        out += line(out[-1], a)[1:]
        for k in range(1, 41):
            t = k / 40
            out.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p1[0] + t * t * b[0],
                        (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p1[1] + t * t * b[1]))
    out += line(out[-1], poly[-1])[1:]
    return out


class Canvas:
    def __init__(self, fill=(0, 0, 0), w=None, h=None):
        self.w, self.h = w or W, h or H
        self.px = [[fill] * self.w for _ in range(self.h)]

    def get(self, x, y):
        return self.px[y][x]

    def put(self, x, y, c, a=1.0):
        if 0 <= x < self.w and 0 <= y < self.h:
            if a >= 1.0:
                self.px[y][x] = c
            else:
                self.px[y][x] = mix(self.px[y][x], c, a)

    def shade(self, x, y, k):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = scale(self.px[y][x], k)

    def each(self, fn, box=None):
        x0, y0, x1, y1 = box or (0, 0, self.w, self.h)
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
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


def totem(cv, x0, y0, stone, glows):
    """Carved stone idol, 12x26."""
    for y in range(y0 + 6, y0 + 26):
        for x in range(x0 + 1, x0 + 11):
            n = 0.85 + 0.25 * hash2(x // 2, y // 3, 71)
            c = scale(stone, n * (1.2 if x == x0 + 1 else 0.7 if x == x0 + 10 else 1.0))
            if (y - y0) % 7 == 0:
                c = scale(stone, 0.6)
            cv.put(x, y, c)
    for x in range(x0, x0 + 12):                # gold headdress
        for y in range(y0, y0 + 6):
            if y - y0 >= abs(x - x0 - 5.5) - 2:
                cv.put(x, y, GOLD if (x + y) % 3 else GOLD_DK)
    for ex in (x0 + 3, x0 + 7):                  # jade eyes
        cv.put(ex, y0 + 9, JADE_LT)
        cv.put(ex + 1, y0 + 9, JADE)
    for x in range(x0 + 3, x0 + 9):              # mouth
        cv.put(x, y0 + 14, (30, 16, 12))
    cv.put(x0 + 4, y0 + 15, BONE)
    cv.put(x0 + 7, y0 + 15, BONE)


def fern(cv, x0, y0, stone, glows):
    """Jungle plant, 18x16."""
    cx, cy = x0 + 9, y0 + 15
    for k in range(7):
        ang = math.pi * (0.1 + 0.8 * k / 6)
        for t in range(12):
            x = cx - math.cos(ang) * t * 0.8
            y = cy - math.sin(ang) * t * 1.1 + (t * t) * 0.03
            col = (40, 110, 40) if t % 3 else (100, 180, 70)
            cv.put(int(x), int(y), col)
            cv.put(int(x) + 1, int(y), (24, 70, 24))


def brazier(cv, x0, y0, stone, glows):
    """Stone fire bowl, 10x16."""
    for y in range(y0 + 8, y0 + 16):
        w = 5 if y < y0 + 11 else 2
        for x in range(x0 + 5 - w, x0 + 5 + w):
            cv.put(x, y, scale(stone, 1.1 if y == y0 + 8 else 0.8))
    disc(cv, x0 + 5, y0 + 5, 3.2, (220, 70, 20))
    disc(cv, x0 + 5, y0 + 5.5, 2.2, (255, 170, 40))
    disc(cv, x0 + 5, y0 + 6, 1.1, (255, 240, 150))
    glows.append((x0 + 5, y0 + 6))


def lava_pool(cv, x0, y0, stone, glows):
    """Bubbling lava, 22x14."""
    cx, cy = x0 + 11, y0 + 7
    for y in range(y0, y0 + 14):
        for x in range(x0, x0 + 22):
            d = math.hypot((x - cx) / 11, (y - cy) / 7)
            if d < 0.8:
                n = fbm(x / 3, y / 3, 81, 2)
                cv.put(x, y, mix((230, 80, 10), (255, 220, 90), n))
            elif d < 1.0:
                cv.put(x, y, (40, 26, 24))
    glows.append((cx, cy))


def pond(cv, x0, y0, stone, glows):
    """Sacred pool with a jade rim, 24x14."""
    cx, cy = x0 + 12, y0 + 7
    for y in range(y0, y0 + 14):
        for x in range(x0, x0 + 24):
            d = math.hypot((x - cx) / 12, (y - cy) / 7)
            if d < 0.78:
                c = mix((30, 80, 150), (60, 140, 210), 1 - d)
                if abs(math.sin(d * 14 + x * 0.2)) > 0.93:
                    c = (160, 210, 240)
                cv.put(x, y, c)
            elif d < 1.0:
                cv.put(x, y, JADE if d < 0.9 else JADE_DK)


def medallion(cv, x0, y0, stone, glows):
    sun_stone(cv, x0 + 10, y0 + 10, 9, stone)


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


def floor_volcano(x, y):
    n = 0.8 + 0.4 * fbm(x / 6, y / 6, 61, 2)
    c = scale((56, 44, 44), n)
    r = abs(fbm(x / 16, y / 16, 62) - 0.5)
    if r < 0.018:
        return mix((255, 210, 80), (230, 70, 10), r / 0.018)       # lava crack
    if r < 0.05:
        return mix(c, (170, 50, 20), (0.05 - r) / 0.05 * 0.8)      # glow around it
    return c


def floor_jade(x, y):
    lx, ly = x % 16, y % 16
    bx, by = x // 16, y // 16
    tone = 0.85 + 0.25 * hash2(bx, by, 91)
    sheen = 0.9 + 0.25 * (1 - (lx + ly) / 30)
    c = scale((56, 150, 120) if (bx + by) % 2 else (40, 124, 104), tone * sheen)
    if lx == 0 or ly == 0:
        return scale(GOLD, 0.8)
    if lx == 1 or ly == 1:
        return scale(c, 0.7)
    if hash2(bx, by, 92) > 0.75 and lx in (5, 10) and 4 <= ly <= 11:
        return scale(c, 0.75)     # carved grooves
    return c


LOGO = {
    "Z": ["#######", "#######", "....##.", "...##..", "..##...", ".##....", "#######", "#######"],
    "U": ["##...##", "##...##", "##...##", "##...##", "##...##", "##...##", "#######", ".#####."],
    "M": ["##...##", "###.###", "#######", "##.#.##", "##...##", "##...##", "##...##", "##...##"],
    "A": ["..###..", ".#####.", "##...##", "##...##", "#######", "#######", "##...##", "##...##"],
    "O": [".#####.", "##...##", "##...##", "##...##", "##...##", "##...##", "##...##", ".#####."],
    "E": ["#######", "#######", "##.....", "######.", "######.", "##.....", "#######", "#######"],
    "R": ["######.", "##...##", "##...##", "######.", "#####..", "##.##..", "##..##.", "##...##"],
}


def glyph_mask(text, cell, gap, glyphs):
    """cell is the pixel size of one glyph cell: an int, or (width, height)."""
    cw, ch_ = cell if isinstance(cell, tuple) else (cell, cell)
    cols = []
    for i, ch in enumerate(text):
        g = glyphs[ch]
        for gx in range(len(g[0])):
            cols.append([g[gy][gx] == "#" for gy in range(len(g))])
        if i < len(text) - 1:
            cols += [[False] * len(g)] * gap
    w = len(cols) * cw
    h = len(cols[0]) * ch_
    return w, h, lambda x, y: 0 <= x < w and 0 <= y < h and cols[x // cw][y // ch_]


def carved_text(cv, text, glyphs, cell, gap, top, face_top, face_bot, edge_lt, edge_dk, outline,
                band=True):
    w, h, m = glyph_mask(text, cell, gap, glyphs)
    cell_h = cell[1] if isinstance(cell, tuple) else cell
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
            elif band and cell_h * 3 <= y < cell_h * 3 + 2:
                c = scale(c, 0.7)       # carved stripe
            elif band and y == cell_h * 3 + 2:
                c = scale(c, 1.15)
            cv.put(x0 + x, top + y, c)


SMALL_GLYPHS = {
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".####"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
}


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


def pal16(cols):
    cols = list(cols) + [(0, 0, 0)] * (16 - len(cols))
    return [rgb15(*c) for c in cols]


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


def write_png(path, rows):
    raw = b"".join(b"\x00" + bytes(int(v) for px in row for v in px) for row in rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw)))
        f.write(chunk(b"IEND", b""))


# ================================================================ DS layouts
BALL_R = 6                 # balls are 12 pixels across on the DS
SPACING = 22               # minimum distance between neighbouring runs of track
FROG_CLEAR = 34            # track centre to frog centre
SERPENT_S = 1.8            # serpent head scale (mouth fits a 12px ball)
MOUTH_DEPTH = int(13 * SERPENT_S)


class Layout:
    def __init__(self, name, dense, frog):
        self.name = name
        self.path = resample(dense)
        self.frog = frog
        self.end = self.path[-1]
        first = next(i for i, (x, y) in enumerate(self.path) if 0 <= x < W and 0 <= y < H)
        self.hide = first + MOUTH_DEPTH            # balls stay hidden inside the serpent's mouth
        x0, y0 = self.path[0]
        self.edge = "left" if x0 < 0 else "right" if x0 >= W else "top" if y0 < 0 else "bottom"
        fx, fy = self.path[first]
        self.entry = (fx, fy)
        self.along = fy if self.edge in ("left", "right") else fx
        # rolling direction at each point, in libnds angle units (32768 a turn)
        n = len(self.path)
        self.angle = []
        for i in range(n):
            ax, ay = self.path[max(0, i - 3)]
            bx, by = self.path[min(n - 1, i + 3)]
            self.angle.append(int(round(math.atan2(by - ay, bx - ax) / (2 * math.pi) * 32768)) & 32767)
        self.d = [[99.0] * W for _ in range(H)]
        self.n = [[(0.0, 0.0)] * W for _ in range(H)]
        for px, py in self.path:
            for y in range(py - 13, py + 14):
                if not 0 <= y < H:
                    continue
                for x in range(px - 13, px + 14):
                    if 0 <= x < W:
                        d = math.hypot(x - px, y - py)
                        if d < self.d[y][x]:
                            self.d[y][x] = d
                            self.n[y][x] = ((x - px) / d, (y - py) / d) if d > 0 else (0.0, 0.0)
        self.validate()

    def validate(self):
        vis = [(i, x, y) for i, (x, y) in enumerate(self.path) if i >= self.hide]
        for i, x, y in vis:
            assert 8 <= x <= W - 9 and 8 <= y <= H - 9, (self.name, "track off screen", x, y)
            d = math.hypot(x - self.frog[0], y - self.frog[1])
            assert d >= FROG_CLEAR, (self.name, "track too close to frog", x, y, d)
        step = 3
        for a in range(0, len(vis), step):
            ia, xa, ya = vis[a]
            for b in range(a + step, len(vis), step):
                ib, xb, yb = vis[b]
                if ib - ia > 40:
                    d = math.hypot(xa - xb, ya - yb)
                    assert d >= SPACING, (self.name, "track overlaps itself", (xa, ya), (xb, yb), d)


LAYOUTS = [
    # the classic: spiral around a centred frog
    Layout("SPIRAL", line((276, 96), (244, 96)) + spiral(128, 96, 116, 56, 84, 40, 0, 1.75, 1), (128, 96)),
    # four rows snaking down towards a frog at the bottom
    Layout("SWITCHBACK", rounded([(-24, 30), (234, 30), (234, 60), (46, 60), (46, 90), (234, 90),
                                  (234, 120), (60, 120)], 15), (128, 162)),
    # long runway along the bottom into a spiral on the left
    Layout("WHIRLPOOL", rounded([(276, 110), (212, 110), (212, 176), (96, 176)], 18) +
           spiral(96, 96, 84, 40, 80, 40, -math.pi / 2, 1.5, -1), (96, 96)),
    # a U inside a U, coming in from the left
    Layout("HORSESHOE", rounded([(-24, 56), (42, 56), (42, 170), (234, 170), (234, 26), (190, 26),
                                 (190, 128), (66, 128), (66, 40)], 18), (128, 78)),
    # columns sweeping right to left towards a frog on the left edge
    Layout("CASCADE", rounded([(276, 40), (214, 40), (214, 170), (172, 170), (172, 22), (130, 22),
                               (130, 170), (88, 170), (88, 40)], 20), (40, 96)),
    # square Aztec-fret spiral, rising from the bottom
    Layout("STEP FRET", rounded([(224, 216), (224, 16), (20, 16), (20, 176), (180, 176),
                                 (180, 58), (62, 58), (62, 138), (110, 138)], 18), (124, 98)),
]


# ================================================================ level art
def draw_track(cv, L, groove, lip):
    for y in range(H):
        for x in range(W):
            d = L.d[y][x]
            if d > 12.5:
                continue
            nx, ny = L.n[y][x]
            lit = nx * LIGHT[0] + ny * LIGHT[1]
            n = 0.9 + 0.2 * hash2(x, y, 3)
            if d <= 6.6:
                ao = 1.0 - 0.4 * (d / 6.6) ** 2
                cv.put(x, y, scale(groove, ao * n))
            elif d <= 8.0:
                cv.put(x, y, scale(lip, (0.8 - 0.45 * lit) * n))    # inner slope faces the centre
            elif d <= 9.4:
                cv.put(x, y, scale(lip, (0.85 + 0.45 * lit) * n))   # outer slope faces out
            elif lit < 0:
                cv.shade(x, y, 1.0 + 0.45 * lit * (12.5 - d) / 3.1)  # drop shadow


def end_hole(cv, hx, hy, r=10.5):
    """Gold-rimmed pit at the end of the track."""
    def col(x, y, d):
        nx, ny = (x + 0.5 - hx) / max(d, 0.1), (y + 0.5 - hy) / max(d, 0.1)
        lit = nx * LIGHT[0] + ny * LIGHT[1]
        if d > r - 0.8:
            return GOLD_DK
        if d > r - 2.8:
            return scale(GOLD, 1.0 + 0.35 * lit)
        return scale((40, 24, 16), max(0.12, 0.6 - 0.5 * lit) * (d / (r - 2.8)))
    disc(cv, hx, hy, r, col)


def _serpent_right(cv, s):
    """Quetzalcoatl head on the right edge of a 256x192 canvas, mouth centred on y=96.
    Shapes are in (u, v): u is the distance in from the edge, v runs along it."""
    def P(u, v):
        return (W - 1 - u * s, 96 + v * s)

    def jade_shade(x, y):
        n = 0.8 + 0.35 * fbm(x * 0.3, y * 0.3, 21, 2)
        return scale(JADE, n)

    for i, c in enumerate([TERRACOTTA, GOLD, JADE_LT, TERRACOTTA]):   # feathered crest
        for v10 in range(int((-20 + i * 3) * s), int((22 - i * 3) * s)):
            for k in range(int(s) + 1):
                x = int(W - 1 - i * s - k)
                cv.put(x, 96 + v10, c if (v10 // 2 + i) % 4 else scale(c, 0.7))
    upper = [P(13, -4), P(10, -10), P(5, -15), P(-1, -17), P(-1, -4)]
    lower = [P(12, 4), P(-1, 4), P(-1, 17), P(4, 15), P(9, 10)]
    fill_poly(cv, upper, jade_shade)
    fill_poly(cv, lower, jade_shade)
    x0 = int(P(13, 0)[0])
    for x in range(x0, W):
        for t in range(2):
            cv.put(x, int(P(0, -4)[1]) + t, JADE_DK)
            cv.put(x, int(P(0, 4)[1]) - t, JADE_DK)
    for y in range(int(P(0, -3)[1]), int(P(0, 3)[1]) + 1):          # mouth
        for x in range(int(P(12, 0)[0]), W):
            edge = y in (int(P(0, -3)[1]), int(P(0, 3)[1]))
            cv.put(x, y, (70, 14, 20) if edge else (40, 8, 12))
    for tu in range(1, 11, 3):                                        # fangs
        fx, fy = P(tu, -3)
        for k in range(3):
            cv.put(int(fx), int(fy) + k, BONE)
            cv.put(int(fx) + 1, int(fy) + k - 1, BONE)
        fx, fy = P(tu + 1, 3)
        for k in range(3):
            cv.put(int(fx), int(fy) - k, BONE)
            cv.put(int(fx) + 1, int(fy) - k + 1, BONE)
    for (su, sv) in [(6, 11), (2, 13), (3, -12), (7, -11)]:          # scales
        sx, sy = P(su, sv)
        disc(cv, sx, sy, 1.2, JADE_LT)
    ex, ey = P(3.5, -10.5)                                            # eye
    disc(cv, ex, ey, 2.4 * s, GOLD)
    disc(cv, ex, ey, 1.1 * s, (20, 10, 10))
    cv.put(int(ex) - 1, int(ey) - 1, GOLD_LT)
    for u10 in range(0, int(8 * s)):                                  # brow
        bx, by = P(8 - u10 / s, -14 + u10 / s / 4)
        cv.put(int(bx), int(by), JADE_DK)
        cv.put(int(bx), int(by) + 1, JADE_DK)


SENTINEL = (1, 2, 3)


def serpent_head(cv, L):
    """Draw the serpent where the track enters, turned so its mouth lines up with
    the stretch of track the balls roll out along, its neck running back off screen."""
    tmp = Canvas(SENTINEL)
    _serpent_right(tmp, SERPENT_S)
    ox, oy = L.path[L.hide - MOUTH_DEPTH]                    # where the track meets the edge
    ex, ey = L.path[min(len(L.path) - 1, L.hide)]            # where the balls come out
    dx, dy = ex - ox, ey - oy
    n = math.hypot(dx, dy)
    dx, dy = dx / n, dy / n
    nx, ny = dy, -dx                                         # the drawing's "v" axis
    # the eye is on the -v side: keep it facing up, or into the screen on a
    # track that runs up or down
    if ny < -0.3 or (abs(ny) <= 0.3 and (nx > 0) == (ox < W / 2)):
        nx, ny = -nx, -ny
    neck_w = 15 * SERPENT_S
    reach = int(30 * SERPENT_S)
    for y in range(max(0, int(oy) - reach), min(H, int(oy) + reach)):
        for x in range(max(0, int(ox) - reach), min(W, int(ox) + reach)):
            rx, ry = x - ox, y - oy
            u, v = rx * dx + ry * dy, rx * nx + ry * ny
            if u < 0:                                        # neck
                if abs(v) > neck_w:
                    continue
                sh = 0.8 + 0.35 * fbm(x * 0.3, y * 0.3, 21, 2)
                col = scale(JADE, sh)
                if abs(v) > neck_w - 2:
                    col = JADE_DK
                elif int(v - u * 0.6 + 400) % 8 == 0:
                    col = scale(JADE_LT, sh)                 # scale bands
                cv.put(x, y, col)
                continue
            sx, sy = int(round(W - 1 - u)), int(round(96 + v))
            if 0 <= sx < W and 0 <= sy < H and tmp.px[sy][sx] != SENTINEL:
                cv.put(x, y, tmp.px[sy][sx])


def medallion(cv, x0, y0, stone, glows):
    sun_stone(cv, x0 + 12, y0 + 12, 11, stone)


def pyramid_decor(cv, x0, y0, stone, glows):
    pyramid(cv, x0, y0 + 30, 38, 5, stone)


DECOR = {  # name: (draw, width, height)
    "pyramid": (pyramid_decor, 38, 30),
    "medallion": (medallion, 24, 24),
    "totem": (totem, 12, 26),
    "fern": (fern, 18, 16),
    "brazier": (brazier, 10, 16),
    "lava": (lava_pool, 22, 14),
    "pond": (pond, 24, 14),
}


def place_scenery(cv, L, t, seed, glows):
    """Scatter decorations wherever the track, frog and serpent leave room."""
    blocked = [[0] * (W + 1) for _ in range(H + 1)]   # summed-area table
    for y in range(H):
        row = 0
        for x in range(W):
            bad = (L.d[y][x] < 13 or
                   math.hypot(x - L.frog[0], y - L.frog[1]) < 30 or
                   math.hypot(x - L.entry[0], y - L.entry[1]) < 48 or
                   math.hypot(x - L.end[0], y - L.end[1]) < 14)
            row += bad
            blocked[y + 1][x + 1] = blocked[y][x + 1] + row

    def free(x, y, w, h):
        return blocked[y + h][x + w] - blocked[y][x + w] - blocked[y + h][x] + blocked[y][x] == 0

    placed = []
    for k, kind in enumerate(t["decor"]):
        draw, w, h = DECOR[kind]
        cands = [(hash2(x, y, seed * 31 + k), x, y)
                 for y in range(2, H - h - 1, 3) for x in range(2, W - w - 1, 3)]
        cands.sort()
        for _, x, y in cands:
            if not free(x, y, w, h):
                continue
            if any(x < px + pw + 4 and px < x + w + 4 and y < py + ph + 4 and py < y + h + 4
                   for px, py, pw, ph in placed):
                continue
            placed.append((x, y, w, h))
            draw(cv, x, y, t["stone"], glows)
            break


THEMES = [
    dict(name="JUNGLE PATH", floor=floor_jungle, groove=(112, 78, 44), lip=(160, 150, 126),
         stone=(160, 150, 130), glow=None, ambient=1.0, accent=(70, 150, 60),
         decor=["pyramid", "fern", "medallion", "fern", "totem", "fern", "fern", "pyramid"]),
    dict(name="SUN TEMPLE", floor=floor_temple, groove=(100, 62, 36), lip=(186, 160, 118),
         stone=(176, 150, 112), glow=None, ambient=1.0, accent=(220, 170, 70),
         decor=["pyramid", "totem", "medallion", "totem", "pyramid", "medallion"]),
    dict(name="MOON RITUAL", floor=floor_night, groove=(34, 30, 40), lip=(110, 118, 140),
         stone=(118, 122, 138), glow=(255, 150, 50), ambient=0.55, accent=(120, 140, 220),
         decor=["brazier", "pyramid", "brazier", "totem", "brazier", "medallion", "brazier"]),
    dict(name="FIRE MOUNTAIN", floor=floor_volcano, groove=(30, 20, 20), lip=(96, 80, 76),
         stone=(110, 92, 86), glow=(255, 90, 30), ambient=0.75, accent=(240, 90, 30),
         decor=["lava", "pyramid", "lava", "totem", "lava", "lava"]),
    dict(name="JADE POOLS", floor=floor_jade, groove=(20, 60, 56), lip=(200, 170, 100),
         stone=(170, 176, 150), glow=None, ambient=1.0, accent=(60, 200, 160),
         decor=["pond", "medallion", "totem", "pond", "pyramid", "pond"]),
]


def apply_glow(cv, t, glows):
    def light(x, y, c):
        k = t["ambient"]
        add = [0.0, 0.0, 0.0]
        for gx, gy in glows:
            d = math.hypot(x - gx, y - gy)
            g = max(0.0, 1 - d / 70) ** 2
            k += 0.9 * g
            for i in range(3):
                add[i] += t["glow"][i] * g * 0.25
        return tuple(clamp(c[i] * k + add[i]) for i in range(3))
    cv.each(light)


def render_level(t, L, seed):
    cv = Canvas()
    cv.each(lambda x, y, c: t["floor"](x, y))
    glows = []
    place_scenery(cv, L, t, seed, glows)
    draw_track(cv, L, t["groove"], t["lip"])
    sun_stone(cv, L.frog[0], L.frog[1], 24, t["stone"])
    end_hole(cv, L.end[0], L.end[1])
    serpent_head(cv, L)
    if t["glow"]:
        glows.append(L.frog)
        apply_glow(cv, t, glows)
    return cv


# ================================================================ title screens
DS_GLYPHS = {
    "D": ["#####..", "##..##.", "##...##", "##...##", "##...##", "##...##", "##..##.", "#####.."],
    "S": [".#####.", "##...##", "##.....", ".#####.", "......#", "......#", "##...##", ".#####."],
}


def temple_wall(x, y):
    bw, bh = 24, 12
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
    return c


def vines(cv, xs, seed):
    for vx in xs:
        length = 30 + int(hash2(vx, 0, seed) * 70)
        for y in range(0, length):
            x = vx + int(2.5 * math.sin(y / 7 + vx))
            cv.put(x, y, (30, 90, 30))
            cv.put(x + 1, y, (60, 140, 50))
            if y % 6 == 0:
                side = 1 if (y // 6) % 2 else -1
                for k in range(1, 4):
                    cv.put(x + side * k, y + k // 2, (70, 160, 60))
                    cv.put(x + side * k, y + k // 2 + 1, (30, 100, 36))


def vignette(cv, cx, cy, sx, sy, lo=0.25):
    def f(x, y, c):
        vx, vy = (x - cx) / sx, (y - cy) / sy
        return scale(c, max(lo, 1 - (vx * vx + vy * vy) * 1.3))
    cv.each(f)


def render_title_top():
    cv = Canvas()
    cv.each(lambda x, y, c: temple_wall(x, y))
    vignette(cv, 128, 80, 150, 130)
    vines(cv, (6, 18, 34, 220, 236, 248), 55)
    sun_stone(cv, 128, 70, 74, (176, 150, 112), dim=0.75)
    carved_text(cv, "ZOOMER", LOGO, (5, 7), 1, 30,
                face_top=(255, 222, 110), face_bot=(214, 120, 30),
                edge_lt=(255, 248, 200), edge_dk=(120, 60, 10), outline=(40, 16, 6))
    carved_text(cv, "DS", DS_GLYPHS, (3, 3), 1, 96,
                face_top=JADE_LT, face_bot=JADE, edge_lt=(210, 255, 230), edge_dk=JADE_DK,
                outline=(20, 12, 6), band=False)
    greca_band(cv, 176, 16, (30, 18, 10), GOLD, TERRACOTTA)
    for x in range(W):
        cv.put(x, 175, GOLD_DK)
    return cv


def render_title_bottom():
    cv = Canvas()
    cv.each(lambda x, y, c: temple_wall(x, y + 7))
    vignette(cv, 128, 96, 170, 150, 0.35)
    greca_band(cv, 0, 12, (30, 18, 10), GOLD, TERRACOTTA)
    greca_band(cv, 180, 12, (30, 18, 10), GOLD, TERRACOTTA)
    for x in range(W):
        cv.put(x, 12, GOLD_DK)
        cv.put(x, 179, GOLD_DK)
    return cv


# ================================================================ top screen dashboard
# Text rows (8px) used by source/main.c for each panel.
DASH = dict(header=(0, 2), score=(3, 9), progress=(10, 13), danger=(14, 17), balls=(18, 23))


def stone_panel(cv, x0, y0, x1, y1, t, seed):
    """A carved stone slab with a gold edge, pixel rectangle [x0,x1) x [y0,y1)."""
    base = scale(t["stone"], 0.55)
    for y in range(y0, y1):
        for x in range(x0, x1):
            n = 0.85 + 0.25 * fbm(x / 5, y / 5, seed, 2)
            c = scale(base, n)
            e = min(x - x0, y - y0, x1 - 1 - x, y1 - 1 - y)
            if e == 0:
                c = (26, 16, 10)
            elif e == 1:
                c = GOLD_DK if (x + y) % 2 else scale(GOLD, 0.8)
            elif e == 2:
                c = scale(base, 1.4) if (x - x0 < 3 or y - y0 < 3) else scale(base, 0.6)
            cv.put(x, y, c)


def render_dashboard(t, seed):
    cv = Canvas()
    cv.each(lambda x, y, c: scale(t["floor"](x, y + 40), 0.55))
    if t["glow"]:
        apply_glow(cv, t, [(20, 180), (236, 180), (128, 100)])
    vines(cv, (4, 250), 90 + seed) if t["name"] == "JUNGLE PATH" else None
    greca_band(cv, 0, 12, (30, 18, 10), GOLD, scale(t["accent"], 0.9))
    for x in range(W):
        cv.put(x, 12, GOLD_DK)
    for key, (r0, r1) in DASH.items():
        if key == "header":
            continue
        stone_panel(cv, 8, r0 * 8 + 2, 248, r1 * 8 + 6, t, seed + r0)
    # sockets for the current and next ball
    for sx in (36, 92):
        disc(cv, sx, 160, 16.5, lambda x, y, d: (20, 12, 8) if d > 15 else scale(t["stone"], 0.35))
    return cv


# ================================================================ bitmaps and compression
def bitmap16(cv):
    """Raw RGB15 pixels with the alpha bit set, little-endian."""
    return b"".join(struct.pack("<H", rgb15(*c) | 0x8000) for row in cv.px for c in row)


def lz77(data):
    """Nintendo LZ77 (type 0x10), safe for 16-bit VRAM writes (no distance-1 matches)."""
    out = bytearray([0x10, len(data) & 255, (len(data) >> 8) & 255, (len(data) >> 16) & 255])
    heads = {}
    i, n = 0, len(data)
    while i < n:
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len, best_dist = 0, 0
            if i + 3 <= n:
                key = data[i:i + 3]
                for j in reversed(heads.get(key, ())):
                    dist = i - j
                    if dist > 4096:
                        break
                    if dist < 2:
                        continue
                    m = 3
                    while m < 18 and i + m < n and data[j + m] == data[i + m]:
                        m += 1
                    if m > best_len:
                        best_len, best_dist = m, dist
                        if m == 18:
                            break
            step = best_len if best_len >= 3 else 1
            if best_len >= 3:
                flags |= 0x80 >> bit
                d = best_dist - 1
                out.append(((best_len - 3) << 4) | (d >> 8))
                out.append(d & 255)
            else:
                out.append(data[i])
            for k in range(i, min(i + step, n - 2)):
                lst = heads.setdefault(data[k:k + 3], [])
                lst.append(k)
                if len(lst) > 48:
                    del lst[:16]
            i += step
        out[flag_pos] = flags
    while len(out) % 4:
        out.append(0)
    return bytes(out)


def unlz77(comp):
    """Decoder, used to check the compressor."""
    n = comp[1] | (comp[2] << 8) | (comp[3] << 16)
    out = bytearray()
    i = 4
    while len(out) < n:
        flags = comp[i]
        i += 1
        for bit in range(8):
            if len(out) >= n:
                break
            if flags & (0x80 >> bit):
                ln = (comp[i] >> 4) + 3
                d = (((comp[i] & 15) << 8) | comp[i + 1]) + 1
                i += 2
                for _ in range(ln):
                    out.append(out[-d])
            else:
                out.append(comp[i])
                i += 1
    return bytes(out)


# ================================================================ 3D textures
GLYPHS.update({
    "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
    "=": "..... ..... ##### ..... ##### ..... .....",
    ".": "..... ..... ..... ..... ..... ..... ..#..",
    "'": "..#.. ..#.. .#... ..... ..... ..... .....",
    ",": "..... ..... ..... ..... ..... ..#.. .#...",
    "%": "##..# ##.#. ...#. ..#.. .#... .#.## #..##",
    "/": "....# ...#. ...#. ..#.. .#... .#... #....",
})

# The playfield is drawn by the DS 3D engine (libnds gl2d): 8-bit paletted
# textures with colour 0 transparent.
BALL_COLORS = [
    (220, 40, 40),    # red
    (40, 190, 60),    # green
    (40, 90, 230),    # blue
    (240, 200, 20),   # yellow
    (170, 50, 210),   # purple
]
BALL_FRAMES = 8
BALL_ROLL_PX = 19          # pixels of travel for the pattern to come round again (half a turn)
L3 = (-0.45, -0.55, 0.70)  # light direction for the 3D look


def rgba_canvas(w, h):
    return [[None] * w for _ in range(h)]


def ball_frame(col, frame):
    """16x16: a 12px ball rolling along +x, with a light band round its middle."""
    img = rgba_canvas(16, 16)
    phi = math.pi * frame / BALL_FRAMES
    band = mix(col, (255, 255, 255), 0.45)
    dark = scale(col, 0.25)
    for y in range(16):
        for x in range(16):
            dx, dy = (x + 0.5 - 8) / 6.0, (y + 0.5 - 8) / 6.0
            d2 = dx * dx + dy * dy
            if d2 > 1.0:
                continue
            z = math.sqrt(1 - d2)
            lit = max(0.0, dx * L3[0] + dy * L3[1] + z * L3[2])
            k = 0.28 + 0.9 * lit
            # rotate the surface point back to find where it sits on the ball's pattern
            mx = dx * math.cos(phi) - z * math.sin(phi)
            base = band if abs(mx) < 0.28 else col
            if 0.24 < abs(mx) < 0.34:
                base = mix(col, dark, 0.5)
            c = scale(base, k)
            if d2 > 0.8:
                c = mix(c, dark, (d2 - 0.8) / 0.2 * 0.8)
            img[y][x] = c
    return img


def disc_img(size, cx, cy, r, colfn):
    img = rgba_canvas(size, size)
    for y in range(size):
        for x in range(size):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d <= r:
                img[y][x] = colfn(d) if callable(colfn) else colfn
    return img


def highlight_img():
    img = rgba_canvas(16, 16)
    for y in range(16):
        for x in range(16):
            d = math.hypot(x + 0.5 - 5.6, y + 0.5 - 5.2)
            if d < 1.7:
                img[y][x] = (255, 255, 255)
            elif d < 2.8:
                img[y][x] = (210, 210, 210)
    return img


def icon_img(kind):
    """Power-up symbol drawn over a ball: 'slow', 'reverse' or 'bomb'."""
    img = rgba_canvas(16, 16)
    ink, edge = (255, 255, 255), (20, 14, 10)

    def dot(x, y, c):
        if 0 <= x < 16 and 0 <= y < 16:
            img[y][x] = c

    def shape(pts):
        for (x, y) in pts:
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    if img[y + oy][x + ox] is None:
                        dot(x + ox, y + oy, edge)
        for (x, y) in pts:
            dot(x, y, ink)

    if kind == "slow":          # hourglass
        pts = []
        for y in range(4, 12):
            w = abs(y - 7.5) - 0.5
            for x in range(int(8 - w - 1), int(8 + w + 1)):
                if y in (4, 11) or x in (int(8 - w - 1), int(8 + w)) or (y > 8 and abs(x - 7.5) < w - 0.5):
                    pts.append((x, y))
        shape(pts)
    elif kind == "reverse":     # double chevron pointing back
        pts = []
        for k in range(4):
            for off in (0, 4):
                pts.append((5 + off + k, 8 - k))
                pts.append((5 + off + k, 7 + k))
        shape(pts)
    else:                       # bomb with a lit fuse
        for y in range(16):
            for x in range(16):
                d = math.hypot(x + 0.5 - 7.5, y + 0.5 - 9)
                if d < 3.6:
                    img[y][x] = (40, 40, 48) if d < 2.9 else edge
        img[7][6] = (150, 150, 170)
        shape([(10, 5), (11, 4)])
        img[3][12] = (255, 220, 80)
        img[2][12] = (255, 140, 40)
    return img


def ring_img(size, r, width):
    return disc_img(size, size / 2, size / 2, r, lambda d: (255, 255, 255) if d > r - width else None)


def star_img():
    img = rgba_canvas(16, 16)
    for y in range(16):
        for x in range(16):
            dx, dy = abs(x + 0.5 - 8), abs(y + 0.5 - 8)
            if dx * dy < 2.2 and dx + dy < 7.5:
                img[y][x] = (255, 255, 255) if dx + dy < 4 else (220, 220, 220)
    return img


FROG_COLS = dict(outline=(14, 50, 20), dark=(30, 100, 36), body=(60, 170, 60), light=(140, 225, 100),
                 belly=(236, 226, 130), eye=(255, 255, 255), pupil=(12, 12, 12), mouth=(150, 26, 36),
                 spot=(36, 120, 50), lid=(46, 140, 50))


def frog_img(blink):
    """64x64, pointing up. The mouth is 15px above the centre, the socket for
    the next ball 13px below it."""
    img = rgba_canvas(64, 64)
    C = FROG_COLS

    def body_col(x, y, d, r):
        dx, dy = (x + 0.5 - 32) / r, (y + 0.5 - 34) / r
        z = math.sqrt(max(0.0, 1 - dx * dx - dy * dy))
        lit = max(0.0, dx * L3[0] + dy * L3[1] + z * L3[2])
        return mix(C["dark"], C["light"], min(1.0, lit * 1.1))

    for y in range(64):
        for x in range(64):
            d = math.hypot(x + 0.5 - 32, y + 0.5 - 34)
            if d <= 19.5:
                img[y][x] = C["outline"] if d > 18.3 else body_col(x, y, d, 18.3)
    # feet
    for fx, fy in ((13, 46), (51, 46), (15, 22), (49, 22)):
        for y in range(64):
            for x in range(64):
                d = math.hypot(x + 0.5 - fx, y + 0.5 - fy)
                if d < 4.2 and img[y][x] is None:
                    img[y][x] = C["outline"] if d > 3.2 else C["dark"]
    # belly and spots
    for y in range(64):
        for x in range(64):
            d = math.hypot((x + 0.5 - 32) / 1.1, y + 0.5 - 38)
            if d < 10:
                img[y][x] = mix(C["belly"], (255, 250, 200), max(0.0, (6 - d) / 12)) if d < 9 else C["dark"]
    for sx, sy, sr in ((20, 34, 2.2), (44, 34, 2.2), (32, 50, 2.6), (24, 45, 1.6), (40, 45, 1.6)):
        for y in range(64):
            for x in range(64):
                if math.hypot(x + 0.5 - sx, y + 0.5 - sy) < sr:
                    img[y][x] = C["spot"]
    # socket for the next ball
    for y in range(64):
        for x in range(64):
            d = math.hypot(x + 0.5 - 32, y + 0.5 - 47)
            if 6.2 < d < 7.4:
                img[y][x] = C["outline"]
    # eyes
    for ex in (21, 43):
        for y in range(64):
            for x in range(64):
                d = math.hypot(x + 0.5 - ex, y + 0.5 - 16)
                if d < 7:
                    if d > 5.8:
                        img[y][x] = C["outline"]
                    elif blink:
                        img[y][x] = C["lid"] if y + 0.5 < 17 else C["outline"] if y < 18 else C["lid"]
                    else:
                        pd = math.hypot(x + 0.5 - ex, y + 0.5 - 15)
                        img[y][x] = C["pupil"] if pd < 2.6 else C["eye"]
        if not blink:
            img[13][ex - 2] = (255, 255, 255)
            img[13][ex - 1] = (255, 255, 255)
    # mouth where the ball sits
    for y in range(64):
        for x in range(64):
            d = math.hypot((x + 0.5 - 32) / 1.2, y + 0.5 - 19)
            if d < 7:
                img[y][x] = C["outline"] if d > 6 else C["mouth"]
    return img


def font_img(ch):
    img = rgba_canvas(8, 8)
    rows = GLYPHS[ch].split()
    pts = [(c + 1, r) for r, row in enumerate(rows) for c, p in enumerate(row) if p == "#"]
    for x, y in pts:
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                if 0 <= x + ox < 8 and 0 <= y + oy < 8 and img[y + oy][x + ox] is None:
                    img[y + oy][x + ox] = (20, 12, 8)
    for x, y in pts:
        img[y][x] = (255, 255, 255)
    return img


def texture(tiles, tw, th, cols, tex_w, tex_h):
    """Pack tiles into an 8-bit texture; returns (palette[256], pixels bytes)."""
    atlas = rgba_canvas(tex_w, tex_h)
    for i, t in enumerate(tiles):
        ox, oy = (i % cols) * tw, (i // cols) * th
        assert oy + th <= tex_h, "texture full"
        for y in range(th):
            for x in range(tw):
                atlas[oy + y][ox + x] = t[y][x]
    opaque = [c for row in atlas for c in row if c is not None]
    cv = Canvas((0, 0, 0), len(opaque), 1)
    cv.px = [opaque]
    pal, rows = quantize(cv, 255)
    it = iter(rows[0])
    pix = bytes(0 if c is None else 1 + next(it) for row in atlas for c in row)
    return [0] + pal + [0] * (255 - len(pal)), pix


TEX_BALL_HIGHLIGHT = len(BALL_COLORS) * BALL_FRAMES
POWERS = ["slow", "reverse", "bomb"]
ball_tiles = [ball_frame(c, f) for c in BALL_COLORS for f in range(BALL_FRAMES)]
ball_tiles += [highlight_img()] + [icon_img(k) for k in POWERS]
ball_tiles += [ring_img(16, 7.5, 1.6),                                             # glow ring
               disc_img(16, 8, 8, 3.2, lambda d: (255, 255, 255) if d < 2 else (200, 200, 200)),  # spark
               disc_img(16, 8, 8, 1.8, (255, 255, 255)),                            # aim dot
               disc_img(16, 8, 8, 6.2, (0, 0, 0)),                                  # shadow
               star_img()]
TEX_NAMES = ["highlight"] + ["pow_" + k for k in POWERS] + ["glow", "spark", "dot", "shadow", "star"]
tex_balls = texture(ball_tiles, 16, 16, 8, 128, 128)
tex_frog = texture([frog_img(False), frog_img(True)], 64, 64, 2, 128, 64)
POPUP_CHARS = "0123456789+X!"
tex_font = texture([font_img(c) for c in POPUP_CHARS], 8, 8, 16, 128, 8)
tex_ring = texture([ring_img(32, 15.5, 2.2)], 32, 32, 1, 32, 32)

# ================================================================ top screen sprites (sub engine, 4bpp)
UI_PAL = [(255, 0, 255), (20, 14, 10), (255, 255, 255), (240, 196, 60), (150, 100, 20),
          (60, 170, 60), (30, 100, 36), (140, 225, 100), (236, 226, 130), (150, 26, 36),
          (40, 40, 48), (120, 140, 220), (240, 90, 30), (12, 12, 12), (14, 50, 20), (200, 200, 200)]


def big_ball_ramp(col):
    """Palette for the 32x32 ball sprite: index 1..15 dark to light, full colour
    through the middle and a white highlight only at the top."""
    ramp = []
    for i in range(15):
        t = i / 14
        ramp.append(mix(scale(col, 0.2), col, t / 0.72) if t < 0.72
                    else mix(col, (255, 255, 255), (t - 0.72) / 0.28 * 0.85))
    return [(0, 0, 0)] + ramp


def big_ball_img():
    img = [[0] * 32 for _ in range(32)]
    for y in range(32):
        for x in range(32):
            dx, dy = (x + 0.5 - 16) / 14.0, (y + 0.5 - 16) / 14.0
            d2 = dx * dx + dy * dy
            if d2 > 1:
                continue
            z = math.sqrt(1 - d2)
            lit = max(0.0, dx * L3[0] + dy * L3[1] + z * L3[2])
            spec = max(0.0, lit - 0.9) * 10
            v = 0.2 + 0.75 * lit + 0.3 * spec
            if d2 > 0.85:
                v *= 0.6
            img[y][x] = max(1, min(15, int(round(v * 14)) + 1))
    return img


def ui_index(c):
    return min(range(1, 16), key=lambda i: sum((a - b) ** 2 for a, b in zip(UI_PAL[i], c)))


def to_ui(img):
    return [[0 if c is None else ui_index(c) for c in row] for row in img]


def upscale(img, k):
    return [[img[y // k][x // k] for x in range(len(img[0]) * k)] for y in range(len(img) * k)]


def icon16(kind):
    base = disc_img(16, 8, 8, 7.5, lambda d: (20, 14, 10) if d > 6.5 else (240, 196, 60))
    ic = icon_img(kind)
    for y in range(16):
        for x in range(16):
            if ic[y][x] is not None:
                base[y][x] = ic[y][x]
    return base


sub_tiles = []
sub_tile_of = {}


def add_sub(name, img):
    h, w = len(img), len(img[0])
    sub_tile_of[name] = len(sub_tiles) // 8
    sub_tiles.extend(to_tiles(img, w, h))


add_sub("ball", big_ball_img())
for k in POWERS:
    add_sub("pow_" + k, to_ui(icon16(k)))
sub_pal = []
for c in BALL_COLORS:
    sub_pal += [rgb15(*v) for v in big_ball_ramp(c)]
sub_pal += pal16(UI_PAL)
sub_pal += [0] * (256 - len(sub_pal))
PAL_SUB_UI = len(BALL_COLORS)

# ================================================================ font (text layers)
FONT_CHARS = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ:!->+=.',%/"
FONT_PAL = [(0, 0, 0), (255, 255, 255), (24, 14, 10), (58, 38, 28), (230, 176, 56),
            (140, 88, 20), (255, 226, 90), (84, 58, 40), (150, 130, 110), (80, 220, 200),
            (230, 60, 40), (30, 110, 100)]
font_pal = pal16(FONT_PAL)
# styles: plain, on a panel, highlighted on a panel, gold (no panel), dimmed on a panel, teal
FONT_STYLES = [(1, 2, 0), (1, 2, 3), (6, 2, 3), (6, 2, 0), (8, 2, 3), (9, 2, 0)]
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


FRAME_TILE = len(font_tiles) // 8
for spec in [(1, 0, 1, 0), (1, 0, 0, 0), (1, 0, 0, 1), (0, 0, 1, 0),
             (0, 0, 0, 1), (0, 1, 1, 0), (0, 1, 0, 0), (0, 1, 0, 1)]:
    font_tiles += frame_tile(*spec)

# progress bar tiles: 0-8 pixels filled, in teal, gold and red
BAR_TILE = len(font_tiles) // 8
for fill_c in (9, 6, 10):
    for n in range(9):
        img = [[0] * 8 for _ in range(8)]
        for x in range(8):
            img[1][x] = img[6][x] = 7
            for y in range(2, 6):
                img[y][x] = (fill_c if y > 2 else 1) if x < n else 3
        font_tiles += to_tiles(img, 8, 8)

# big digits for the score: 16 pixels tall, drawn 4 pixels down in a 2x3 tile
# cell so there's breathing room above and below them
BIGDIGIT_TILE = len(font_tiles) // 8
for d in "0123456789":
    img = [[0] * 16 for _ in range(24)]
    for layer, col in ((1, 2), (0, None)):
        for r, row in enumerate(GLYPHS[d].split()):
            for c, p in enumerate(row):
                if p == "#":
                    for yy in range(2):
                        for xx in range(2):
                            img[5 + r * 2 + yy + layer][3 + c * 2 + xx + layer] = col or (6 if r < 3 else 4)
    font_tiles += to_tiles(img, 16, 24)       # tiles in rows: TL, TR, ML, MR, BL, BR
assert len(font_tiles) * 4 <= 28 * 1024, "font overlaps the text maps"

# ================================================================ DS menu icon
def write_icon_bmp(path, img, pal):
    """32x32 16-colour BMP for the DS menu icon (magenta = transparent)."""
    rows = b""
    for y in range(31, -1, -1):
        for x in range(0, 32, 2):
            rows += bytes([(img[y][x] << 4) | img[y][x + 1]])
    colours = b"".join(bytes([b, g, r, 0]) for (r, g, b) in pal)
    off = 14 + 40 + len(colours)
    hdr = b"BM" + struct.pack("<IHHI", off + len(rows), 0, 0, off)
    info = struct.pack("<IiiHHIIiiII", 40, 32, 32, 1, 4, 0, len(rows), 2835, 2835, 16, 16)
    with open(path, "wb") as f:
        f.write(hdr + info + colours + rows)


def icon_face():
    """The frog's face drawn pixel by pixel at 32x32 (shrinking bigger art loses
    the one-pixel details), in UI_PAL colours."""
    C = FROG_COLS
    img = [[None] * 32 for _ in range(32)]
    for y in range(32):
        for x in range(32):
            d = math.hypot((x + 0.5 - 16) / 14.5, (y + 0.5 - 20) / 11)
            if d < 1:
                img[y][x] = C["outline"] if d > 0.9 else C["light"] if y < 15 else C["body"]
                if math.hypot((x + 0.5 - 16) / 8, (y + 0.5 - 25) / 4.5) < 1:
                    img[y][x] = C["belly"]
    for ex in (9, 23):
        for y in range(32):
            for x in range(32):
                d = math.hypot(x + 0.5 - ex, y + 0.5 - 9)
                if d < 6:
                    img[y][x] = C["outline"] if d > 4.9 else C["eye"]
                    if math.hypot(x + 0.5 - (ex + (1 if ex < 16 else -1)), y + 0.5 - 9.5) < 2.3:
                        img[y][x] = C["pupil"]
        img[7][ex - 1 if ex < 16 else ex - 2] = C["eye"]          # glint
    prev = None
    for x in range(9, 24):                                     # smile
        y = round(20 - (x - 16) ** 2 / 16)
        for yy in range(min(y, prev if prev is not None else y), max(y, prev if prev is not None else y) + 1):
            img[yy][x] = C["outline"]
        prev = y
    for cx in (6, 26):                                         # cheeks
        img[19][cx] = img[19][cx - 1] = C["mouth"]
    return img


write_icon_bmp(os.path.join(ROOT, "icon.bmp"), to_ui(icon_face()), UI_PAL)

# ================================================================ render the pictures
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
PICTURES = {}                  # name -> Canvas, kept for the previews


def save_picture(name, cv):
    raw = bitmap16(cv)
    comp = lz77(raw)
    assert unlz77(comp) == raw, name
    with open(os.path.join(ROOT, "data", name + ".bin"), "wb") as f:
        f.write(comp)
    PICTURES[name] = cv
    return len(comp)


total = 0
total += save_picture("title_top", render_title_top())
total += save_picture("title_bottom", render_title_bottom())
for ti, t in enumerate(THEMES):
    total += save_picture(f"dash_{ti}", render_dashboard(t, 300 + ti * 7))
for li, L in enumerate(LAYOUTS):
    for ti, t in enumerate(THEMES):
        total += save_picture(f"level_{li}_{ti}", render_level(t, L, li * 7 + ti))
print(f"pictures: {len(PICTURES)}, {total // 1024} KB compressed")


# ================================================================ emit C
def c_array(ctype, name, vals, per_line=12, fmt="{}", static=False):
    lines = []
    for i in range(0, len(vals), per_line):
        lines.append("    " + ", ".join(fmt.format(v) for v in vals[i:i + per_line]) + ",")
    return "%sconst %s %s[%d] = {\n%s\n};\n" % ("static " if static else "", ctype, name, len(vals), "\n".join(lines))


NL, NT = len(LAYOUTS), len(THEMES)
hdr = f"""// Generated by tools/gen_assets.py - do not edit.
#ifndef ASSETS_H
#define ASSETS_H

#include <stdint.h>

// ---- tracks
#define NUM_LAYOUTS {NL}
#define NUM_THEMES {NT}
#define BALL_PX {BALL_R * 2}
#define BALL_ROLL_PX {BALL_ROLL_PX}
#define NUM_COLORS {len(BALL_COLORS)}

typedef struct {{
    const char *name;
    const int16_t *x, *y;   // path points, 1px apart
    const uint16_t *angle;  // rolling direction at each point (32768 a turn)
    int16_t len;
    int16_t hide;           // balls before this point are inside the serpent
    int16_t frog_x, frog_y;
}} Layout;

extern const Layout layouts[NUM_LAYOUTS];
extern const char *const theme_names[NUM_THEMES];
extern const uint16_t ball_rgb[NUM_COLORS];     // for tinting sparks

// ---- LZ77-compressed 256x192 RGB15 pictures (data/*.bin)
extern const uint8_t *const level_pic[NUM_LAYOUTS][NUM_THEMES];
extern const uint8_t *const dash_pic[NUM_THEMES];
extern const uint8_t *const title_top_pic, *const title_bottom_pic;

// ---- 3D textures (8-bit, colour 0 transparent)
#define TEX_BALL(color, frame) ((color) * {BALL_FRAMES} + (frame))   // 16x16 tiles in tex_balls
#define BALL_FRAMES {BALL_FRAMES}
"""
for i, nme in enumerate(TEX_NAMES):
    hdr += f"#define TEX_{nme.upper()} {TEX_BALL_HIGHLIGHT + i}\n"
hdr += f"""#define TEX_POW(p) (TEX_POW_SLOW + (p) - 1)
#define NUM_BALL_TILES {len(ball_tiles)}
#define POPUP_CHARS "{POPUP_CHARS}"
extern const uint16_t tex_balls_pal[256], tex_frog_pal[256], tex_font_pal[256], tex_ring_pal[256];
extern const uint8_t tex_balls[128 * 128], tex_frog[128 * 64], tex_font[128 * 8], tex_ring[32 * 32];

// ---- text layers
#define FONT_CHARS "{FONT_CHARS.replace(chr(39), chr(92) + chr(39))}"
#define FONT_NCHARS {len(FONT_CHARS)}
#define FRAME_TILE {FRAME_TILE}
#define BAR_TILE(n, kind) ({BAR_TILE} + (kind) * 9 + (n))   // n of 8 pixels full; teal, gold, red
#define BIGDIGIT_TILE(d) ({BIGDIGIT_TILE} + (d) * 6)       // 2x3 tile digit cell: TL, TR, ML, MR, BL, BR
extern const uint16_t font_pal[16];
extern const uint32_t font_tiles[{len(font_tiles)}];

// ---- top screen sprites (4bpp)
#define SUB_TILE_BALL {sub_tile_of["ball"]}                 // 32x32, palette = colour
#define SUB_TILE_POW(p) ({sub_tile_of["pow_slow"]} + ((p) - 1) * 4)   // 16x16
#define PAL_SUB_UI {PAL_SUB_UI}
extern const uint16_t sub_pal[256];
extern const uint32_t sub_tiles[{len(sub_tiles)}];

// ---- top screen layout (text rows), matching the dashboard pictures
"""
for k, (r0, r1) in DASH.items():
    hdr += f"#define DASH_{k.upper()}_ROW {r0}\n"
hdr += "\n#endif\n"

tracks = "// Generated by tools/gen_assets.py - do not edit.\n#include \"assets.h\"\n\n"
src = tracks
for i, L in enumerate(LAYOUTS):
    tracks += c_array("int16_t", f"path{i}_x", [p[0] for p in L.path], 16, static=True)
    tracks += c_array("int16_t", f"path{i}_y", [p[1] for p in L.path], 16, static=True)
    tracks += c_array("uint16_t", f"path{i}_a", L.angle, 16, static=True)
tracks += "const Layout layouts[NUM_LAYOUTS] = {\n"
for i, L in enumerate(LAYOUTS):
    tracks += (f'    {{ "{L.name}", path{i}_x, path{i}_y, path{i}_a, {len(L.path)}, {L.hide}, '
            f'{L.frog[0]}, {L.frog[1]} }},\n')
tracks += "};\n"
tracks += "const char *const theme_names[NUM_THEMES] = { " + ", ".join(f'"{t["name"]}"' for t in THEMES) + " };\n"
tracks += c_array("uint16_t", "ball_rgb", [rgb15(*c) for c in BALL_COLORS], 8, "0x{:04X}")
for nme, (pal, pix) in (("tex_balls", tex_balls), ("tex_frog", tex_frog), ("tex_font", tex_font), ("tex_ring", tex_ring)):
    src += c_array("uint16_t", nme + "_pal", pal, 8, "0x{:04X}")
    src += "__attribute__((aligned(4))) " + c_array("uint8_t", nme, list(pix), 32)
src += c_array("uint16_t", "font_pal", font_pal, 8, "0x{:04X}")
src += c_array("uint32_t", "font_tiles", font_tiles, 8, "0x{:08X}")
src += c_array("uint16_t", "sub_pal", sub_pal, 8, "0x{:04X}")
src += c_array("uint32_t", "sub_tiles", sub_tiles, 8, "0x{:08X}")
# pictures come from data/*.bin through bin2o
names = list(PICTURES)
src += "\n" + "".join(f"extern const uint8_t {n}_bin[];\n" for n in names)
src += "const uint8_t *const level_pic[NUM_LAYOUTS][NUM_THEMES] = {\n"
for li in range(NL):
    src += "    { " + ", ".join(f"level_{li}_{ti}_bin" for ti in range(NT)) + " },\n"
src += "};\n"
src += "const uint8_t *const dash_pic[NUM_THEMES] = { " + ", ".join(f"dash_{ti}_bin" for ti in range(NT)) + " };\n"
src += "const uint8_t *const title_top_pic = title_top_bin, *const title_bottom_pic = title_bottom_bin;\n"

with open(os.path.join(ROOT, "include", "assets.h"), "w") as f:
    f.write(hdr)
with open(os.path.join(ROOT, "source", "assets.c"), "w") as f:
    f.write(src)
with open(os.path.join(ROOT, "source", "tracks.c"), "w") as f:
    f.write(tracks)
for L in LAYOUTS:
    print(f"{L.name:11s} length {len(L.path):4d} px  frog {L.frog}  enters {L.edge}")


# ================================================================ quick previews
def ds_preview(name, top, bottom, k=2):
    gap = [[(12, 12, 16)] * W for _ in range(10)]
    rows = [list(r) for r in top.px] + gap + [list(r) for r in bottom.px]
    write_png(os.path.join(ROOT, "build", name),
              [[rows[y // k][x // k] for x in range(W * k)] for y in range(len(rows) * k)])


ds_preview("preview_title.png", PICTURES["title_top"], PICTURES["title_bottom"])
for li in range(NL):
    ti = li % NT
    ds_preview(f"preview_level{li + 1}.png", PICTURES[f"dash_{ti}"], PICTURES[f"level_{li}_{ti}"], 1)
