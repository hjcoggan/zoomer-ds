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


# ---------------------------------------------------------------- track layouts
HUD_H = 10
LIGHT = (-0.6, -0.8)   # light from the top left


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


class Layout:
    def __init__(self, name, dense, frog):
        self.name = name
        self.path = resample(dense)
        self.frog = frog
        self.end = self.path[-1]
        first = next(i for i, (x, y) in enumerate(self.path) if 0 <= x < W and HUD_H <= y < H)
        self.hide = first + 12            # balls stay hidden inside the serpent's mouth
        x0, y0 = self.path[0]
        self.edge = "left" if x0 < 0 else "right" if x0 >= W else "top" if y0 < HUD_H else "bottom"
        fx, fy = self.path[first]
        self.entry = (fx, fy)
        self.along = fy if self.edge in ("left", "right") else fx
        self.d = [[99.0] * W for _ in range(H)]
        self.n = [[(0.0, 0.0)] * W for _ in range(H)]
        for px, py in self.path:
            for y in range(py - 10, py + 11):
                if not 0 <= y < H:
                    continue
                for x in range(px - 10, px + 11):
                    if 0 <= x < W:
                        d = math.hypot(x - px, y - py)
                        if d < self.d[y][x]:
                            self.d[y][x] = d
                            self.n[y][x] = ((x - px) / d, (y - py) / d) if d > 0 else (0.0, 0.0)
        self.validate()

    def validate(self):
        vis = [(i, x, y) for i, (x, y) in enumerate(self.path) if i >= self.hide - 12]
        for i, x, y in vis:
            assert 6 <= x <= 233 or i < self.hide, (self.name, "x out of bounds", x, y)
            assert HUD_H + 6 <= y <= 153 or i < self.hide, (self.name, "y out of bounds", x, y)
            d = math.hypot(x - self.frog[0], y - self.frog[1])
            assert d >= 27, (self.name, "track too close to frog", x, y, d)
        step = 3
        for a in range(0, len(vis), step):
            ia, xa, ya = vis[a]
            for b in range(a + step, len(vis), step):
                ib, xb, yb = vis[b]
                if ib - ia > 30:
                    d = math.hypot(xa - xb, ya - yb)
                    assert d >= 15, (self.name, "track overlaps itself", (xa, ya), (xb, yb))


LAYOUTS = [
    # the classic: spiral around a centred frog
    Layout("spiral", line((256, 84), (220, 84)) + spiral(120, 84, 100, 44, 68, 30, 0, 1.75, 1), (120, 84)),
    # three rows snaking down towards a frog at the bottom
    Layout("rows", rounded([(-16, 28), (212, 28), (212, 62), (28, 62), (28, 96), (200, 96)], 16), (120, 136)),
    # long runway along the bottom into a spiral on the left
    Layout("side spiral", line((256, 146), (84, 146)) +
           spiral(84, 88, 70, 32, 58, 30, -math.pi / 2, 1.5, -1), (84, 88)),
    # a U inside a U, dropping in from the top
    Layout("horseshoe", rounded([(20, -16), (20, 140), (220, 140), (220, 30), (180, 30),
                                 (180, 108), (60, 108), (60, 40)], 16), (120, 66)),
    # columns sweeping right to left towards a frog on the left edge
    Layout("columns", rounded([(212, -16), (212, 140), (172, 140), (172, 28), (132, 28),
                               (132, 140), (92, 140), (92, 40)], 18), (38, 84)),
    # square Aztec-fret spiral
    Layout("fret", rounded([(-16, 146), (222, 146), (222, 22), (20, 22), (20, 112),
                            (188, 112), (188, 56), (90, 56)], 16), (126, 84)),
]


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


def _serpent_right(cv):
    """Quetzalcoatl head on the right edge, mouth centred on y=84."""
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


SENTINEL = (1, 2, 3)


def serpent_head(cv, L):
    """Draw the serpent on whichever edge the track enters from."""
    tmp = Canvas(SENTINEL)
    _serpent_right(tmp)
    c = L.along
    for y in range(56, 112):
        for x in range(206, 240):
            col = tmp.px[y][x]
            if col == SENTINEL:
                continue
            u, v = 239 - x, y - 84       # u: distance in from the edge
            if L.edge == "right":
                dx, dy = 239 - u, c + v
            elif L.edge == "left":
                dx, dy = u, c + v
            elif L.edge == "top":
                dx, dy = c + v, HUD_H + u
            else:
                dx, dy = c + v, 159 - u
            cv.put(dx, dy, col)


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


def draw_track(cv, L, groove, lip):
    for y in range(H):
        for x in range(W):
            d = L.d[y][x]
            if d > 9.5:
                continue
            nx, ny = L.n[y][x]
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


# ---------------------------------------------------------------- scenery
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


def pyramid_decor(cv, x0, y0, stone, glows):
    pyramid(cv, x0, y0 + 22, 28, 4, stone)


DECOR = {  # name: (draw, width, height)
    "pyramid": (pyramid_decor, 28, 22),
    "medallion": (medallion, 20, 20),
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
            bad = (L.d[y][x] < 10.5 or y < HUD_H + 2 or
                   math.hypot(x - L.frog[0], y - L.frog[1]) < 25 or
                   math.hypot(x - L.entry[0], y - L.entry[1]) < 28)
            row += bad
            blocked[y + 1][x + 1] = blocked[y][x + 1] + row

    def free(x, y, w, h):
        return blocked[y + h][x + w] - blocked[y][x + w] - blocked[y + h][x] + blocked[y][x] == 0

    placed = []
    kinds = t["decor"]
    for k, kind in enumerate(kinds):
        draw, w, h = DECOR[kind]
        cands = [(hash2(x, y, seed * 31 + k), x, y)
                 for y in range(HUD_H + 2, H - h + 1, 3) for x in range(1, W - w, 3)]
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


THEMES = [
    dict(name="jungle", floor=floor_jungle, groove=(112, 78, 44), lip=(160, 150, 126),
         stone=(160, 150, 130), glow=None, ambient=1.0,
         decor=["pyramid", "fern", "medallion", "fern", "totem", "fern"]),
    dict(name="temple", floor=floor_temple, groove=(100, 62, 36), lip=(186, 160, 118),
         stone=(176, 150, 112), glow=None, ambient=1.0,
         decor=["pyramid", "totem", "medallion", "totem", "pyramid"]),
    dict(name="night", floor=floor_night, groove=(34, 30, 40), lip=(110, 118, 140),
         stone=(118, 122, 138), glow=(255, 150, 50), ambient=0.55,
         decor=["brazier", "pyramid", "brazier", "totem", "brazier", "medallion"]),
    dict(name="volcano", floor=floor_volcano, groove=(30, 20, 20), lip=(96, 80, 76),
         stone=(110, 92, 86), glow=(255, 90, 30), ambient=0.75,
         decor=["lava", "pyramid", "lava", "totem", "lava"]),
    dict(name="jade", floor=floor_jade, groove=(20, 60, 56), lip=(200, 170, 100),
         stone=(170, 176, 150), glow=None, ambient=1.0,
         decor=["pond", "medallion", "totem", "pond", "pyramid"]),
]


def render_level(t, L, seed):
    cv = Canvas()
    cv.each(lambda x, y, c: t["floor"](x, y))
    glows = []
    place_scenery(cv, L, t, seed, glows)
    draw_track(cv, L, t["groove"], t["lip"])
    sun_stone(cv, L.frog[0], L.frog[1], 20, t["stone"])
    end_hole(cv, L.end[0], L.end[1])
    serpent_head(cv, L)
    if t["glow"]:
        glows.append(L.frog)
        def light(x, y, c):
            k = t["ambient"]
            add = [0.0, 0.0, 0.0]
            for gx, gy in glows:
                d = math.hypot(x - gx, y - gy)
                g = max(0.0, 1 - d / 60) ** 2
                k += 0.9 * g
                for i in range(3):
                    add[i] += t["glow"][i] * g * 0.25
            return tuple(clamp(c[i] * k + add[i]) for i in range(3))
        cv.each(light)
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
level_imgs = [[bg_image(render_level(t, L, li * 7 + ti)) for ti, t in enumerate(THEMES)]
              for li, L in enumerate(LAYOUTS)]

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
# styles: plain, on a panel, highlighted on a panel, gold (no panel)
FONT_STYLES = [(1, 2, 0), (1, 2, 3), (6, 2, 3), (6, 2, 0)]
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


NL, NT = len(LAYOUTS), len(THEMES)
IMG_WORDS = len(title_tiles)
hdr = f"""// Generated by tools/gen_assets.py - do not edit.
#ifndef ASSETS_H
#define ASSETS_H

#include <stdint.h>

#define TITLE_FROG_X {TITLE_FROG[0]}
#define TITLE_FROG_Y {TITLE_FROG[1]}
#define FROG_TILE {FROG_TILE}
#define NUM_COLORS {len(BALL_COLORS)}
#define FROG_PALBANK {len(BALL_COLORS)}
#define FONT_CHARS "{FONT_CHARS}"
#define FONT_NCHARS {len(FONT_CHARS)}
#define FRAME_TILE {len(FONT_CHARS) * len(FONT_STYLES)}
#define NUM_LAYOUTS {NL}
#define NUM_THEMES {NT}
#define BG_FIRST_COLOR {BG_FIRST_COLOR}
#define BG_IMG_WORDS {IMG_WORDS}

typedef struct {{
    const int16_t *x, *y;   // path points, 1px apart
    int16_t len;
    int16_t hide;           // balls before this point are inside the serpent
    int16_t frog_x, frog_y;
}} Layout;

extern const Layout layouts[NUM_LAYOUTS];
extern const int16_t sin_tab[256];
extern const uint16_t font_pal[16];
extern const uint16_t obj_pal[{len(obj_pal)}];
extern const uint16_t title_pal[256];
extern const uint32_t title_tiles[BG_IMG_WORDS];
extern const uint16_t *const level_pal[NUM_LAYOUTS][NUM_THEMES];
extern const uint32_t *const level_tiles[NUM_LAYOUTS][NUM_THEMES];
extern const uint32_t obj_tiles[{len(obj_tiles)}];
extern const uint32_t font_tiles[{len(font_tiles)}];

#endif
"""

src = "// Generated by tools/gen_assets.py - do not edit.\n#include \"assets.h\"\n\n"
for i, L in enumerate(LAYOUTS):
    src += c_array("int16_t", f"path{i}_x", [p[0] for p in L.path], 16).replace("const", "static const", 1)
    src += c_array("int16_t", f"path{i}_y", [p[1] for p in L.path], 16).replace("const", "static const", 1)
src += "const Layout layouts[NUM_LAYOUTS] = {\n"
for i, L in enumerate(LAYOUTS):
    src += f"    {{ path{i}_x, path{i}_y, {len(L.path)}, {L.hide}, {L.frog[0]}, {L.frog[1]} }},  // {L.name}\n"
src += "};\n"
src += c_array("int16_t", "sin_tab", sin_tab, 16)
src += c_array("uint16_t", "font_pal", font_pal, 8, "0x{:04X}")
src += c_array("uint16_t", "obj_pal", obj_pal, 8, "0x{:04X}")
src += c_array("uint16_t", "title_pal", title_pal, 8, "0x{:04X}")
src += c_array("uint32_t", "title_tiles", title_tiles, 8, "0x{:08X}")
src += c_array("uint32_t", "obj_tiles", obj_tiles, 8, "0x{:08X}")
src += c_array("uint32_t", "font_tiles", font_tiles, 8, "0x{:08X}")

lvl = "// Generated by tools/gen_assets.py - do not edit.\n#include \"assets.h\"\n\n"
for li in range(NL):
    for ti in range(NT):
        pal, tiles, _ = level_imgs[li][ti]
        lvl += c_array("uint16_t", f"pal_{li}_{ti}", pal, 8, "0x{:04X}").replace("const", "static const", 1)
        lvl += c_array("uint32_t", f"tiles_{li}_{ti}", tiles, 8, "0x{:08X}").replace("const", "static const", 1)
lvl += "const uint16_t *const level_pal[NUM_LAYOUTS][NUM_THEMES] = {\n"
for li in range(NL):
    lvl += "    { " + ", ".join(f"pal_{li}_{ti}" for ti in range(NT)) + " },\n"
lvl += "};\nconst uint32_t *const level_tiles[NUM_LAYOUTS][NUM_THEMES] = {\n"
for li in range(NL):
    lvl += "    { " + ", ".join(f"tiles_{li}_{ti}" for ti in range(NT)) + " },\n"
lvl += "};\n"

with open(os.path.join(ROOT, "include", "assets.h"), "w") as f:
    f.write(hdr)
with open(os.path.join(ROOT, "source", "assets.c"), "w") as f:
    f.write(src)
with open(os.path.join(ROOT, "source", "assets_levels.c"), "w") as f:
    f.write(lvl)


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


def render_preview(pal, idx, frog_at, path, balls, text=()):
    rgb = pal_to_rgb(pal)
    img = [[rgb[idx[y][x]] for x in range(W)] for y in range(H)]

    def blit(spr, sx, sy, p):
        for y, row in enumerate(spr):
            for x, v in enumerate(row):
                if v and 0 <= sx + x < W and 0 <= sy + y < H:
                    img[sy + y][sx + x] = p[v]

    blit(frog, frog_at[0] - 16, frog_at[1] - 16, FROG_PAL)
    for i in range(balls):
        px, py = path[i * 8]
        blit(ball, px - 4, py - 4, [(0, 0, 0)] + BALL_COLORS[(i * 7 // 3) % 5])
    for tx, ty, s in text:
        for i, ch in enumerate(s):
            rows = GLYPHS.get(ch, ". " * 7).split()
            for r, row in enumerate(rows):
                for c, p in enumerate(row):
                    if p == "#":
                        img[ty * 8 + r + 1][(tx + i) * 8 + c + 2] = FONT_PAL[2]
                        img[ty * 8 + r][(tx + i) * 8 + c + 1] = FONT_PAL[1]
    return img


def sheet(name, imgs, cols, k=1):
    rows = (len(imgs) + cols - 1) // cols
    out = [[(0, 0, 0)] * (cols * (W + 4) * k) for _ in range(rows * (H + 4) * k)]
    for n, img in enumerate(imgs):
        ox, oy = (n % cols) * (W + 4), (n // cols) * (H + 4)
        for y in range(H * k):
            for x in range(W * k):
                out[oy * k + y][ox * k + x] = img[y // k][x // k]
    write_png(os.path.join(ROOT, "build", f"preview_{name}.png"), out)


os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
sheet("title", [render_preview(title_pal, title_idx, TITLE_FROG, [], 0, [(9, 15, "PRESS START")])], 1, 3)
levels = []
for lv in range(NL):
    li, ti = lv % NL, lv % NT
    L = LAYOUTS[li]
    pal, _, idx = level_imgs[li][ti]
    levels.append(render_preview(pal, idx, L.frog, L.path[L.hide:], 25,
                                 [(0, 0, "SCORE 000120"), (21, 0, "LEVEL %02d" % (lv + 1))]))
sheet("levels", levels, 2)
themes = []
for ti in range(NT):
    L = LAYOUTS[0]
    pal, _, idx = level_imgs[0][ti]
    themes.append(render_preview(pal, idx, L.frog, L.path[L.hide:], 0))
sheet("themes", themes, 3)
for L in LAYOUTS:
    print(f"{L.name:12s} length {len(L.path):4d} px  frog {L.frog}  enters {L.edge}")
