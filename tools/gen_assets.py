#!/usr/bin/env python3
"""Generate source/assets.c and include/assets.h: path, sine table, palettes,
background, sprite and font tiles. Also writes build/preview.png for a quick look.

Run from the repo root:  python3 tools/gen_assets.py
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 240, 160
CX, CY = 120, 84  # frog position


def rgb15(r, g, b):
    return (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)


# ---------------------------------------------------------------- path
def build_path():
    theta_end = 3.5 * math.pi
    dense = [(256.0, CY)]
    x = 256.0
    while x > 228.0:
        x -= 0.25
        dense.append((x, CY))
    steps = 20000
    for i in range(steps + 1):
        t = theta_end * i / steps
        f = t / theta_end
        rx = 108 - 64 * f
        ry = 68 - 38 * f
        dense.append((CX + rx * math.cos(t), CY - ry * math.sin(t)))
    # resample at 1px arc length
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

# ---------------------------------------------------------------- palettes
BG_PAL = [
    (0, 0, 0),        # 0 backdrop
    (34, 92, 40),     # 1 grass dark
    (52, 120, 52),    # 2 grass light
    (70, 44, 20),     # 3 track edge
    (140, 104, 60),   # 4 track fill
    (8, 8, 8),        # 5 hole
    (220, 170, 40),   # 6 hole rim
    (150, 150, 140),  # 7 stone
    (96, 96, 90),     # 8 stone dark
    (20, 16, 12),     # 9 tunnel
]
FONT_PAL = [(0, 0, 0), (255, 255, 255), (30, 30, 30)]

BALL_COLORS = [
    [(120, 10, 10), (220, 40, 40), (255, 120, 110), (255, 230, 230)],   # red
    [(10, 90, 20), (40, 190, 60), (140, 240, 130), (230, 255, 230)],    # green
    [(10, 30, 120), (40, 90, 230), (120, 170, 255), (230, 240, 255)],   # blue
    [(130, 100, 0), (240, 200, 20), (255, 240, 120), (255, 255, 230)],  # yellow
    [(80, 10, 110), (170, 50, 210), (220, 140, 250), (250, 230, 255)],  # purple
]
FROG_PAL = [(0, 0, 0), (20, 70, 20), (50, 160, 50), (130, 220, 100),
            (230, 220, 120), (255, 255, 255), (10, 10, 10), (170, 30, 40)]


def pal16(cols):
    cols = list(cols) + [(0, 0, 0)] * (16 - len(cols))
    return [rgb15(*c) for c in cols]


bg_pal = pal16(BG_PAL) + pal16(FONT_PAL)
obj_pal = []
for c in BALL_COLORS:
    obj_pal += pal16([(0, 0, 0)] + c)
obj_pal += pal16(FROG_PAL)

# ---------------------------------------------------------------- background
bg = [[0] * W for _ in range(H)]
for y in range(H):
    for x in range(W):
        h = (x * 73856093 ^ y * 19349663) & 0xFFFF
        bg[y][x] = 2 if h % 7 == 0 else 1

# frog platform
for y in range(H):
    for x in range(W):
        d = math.hypot(x - CX, y - CY)
        if d < 16:
            bg[y][x] = 7
        elif d < 18:
            bg[y][x] = 8

# track: min distance stamp
dist = [[99.0] * W for _ in range(H)]
for px, py in PATH:
    for y in range(py - 7, py + 8):
        if not 0 <= y < H:
            continue
        for x in range(px - 7, px + 8):
            if 0 <= x < W:
                d = math.hypot(x - px, y - py)
                if d < dist[y][x]:
                    dist[y][x] = d
for y in range(H):
    for x in range(W):
        if dist[y][x] <= 4.6:
            bg[y][x] = 4
        elif dist[y][x] <= 6.2:
            bg[y][x] = 3

# tunnel entrance at the right edge
for y in range(H):
    for x in range(226, W):
        dy = abs(y - CY)
        if dy <= 6:
            bg[y][x] = 9
        elif dy <= 8:
            bg[y][x] = 8

# hole at the end of the path
for y in range(H):
    for x in range(W):
        d = math.hypot(x - END[0], y - END[1])
        if d < 6.5:
            bg[y][x] = 5
        elif d < 8.5:
            bg[y][x] = 6


def to_tiles(img, w, h):
    """4bpp tiles, row-major tile order, returned as a list of u32 words."""
    words = []
    for ty in range(h // 8):
        for tx in range(w // 8):
            for r in range(8):
                v = 0
                for c in range(8):
                    v |= (img[ty * 8 + r][tx * 8 + c] & 15) << (4 * c)
                words.append(v)
    return words


bg_tiles = to_tiles(bg, W, H)

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


def disc(img, cx, cy, r, col):
    for y in range(len(img)):
        for x in range(len(img[0])):
            if math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= r:
                img[y][x] = col


disc(frog, 16, 17, 12.5, 1)
disc(frog, 16, 17, 11.5, 2)
disc(frog, 16, 19, 7, 4)
disc(frog, 16, 26, 3.5, 1)      # socket for the next ball
disc(frog, 9.5, 9, 4, 1)
disc(frog, 22.5, 9, 4, 1)
disc(frog, 9.5, 9, 3, 5)
disc(frog, 22.5, 9, 3, 5)
disc(frog, 9.5, 8.5, 1.3, 6)
disc(frog, 22.5, 8.5, 1.3, 6)
disc(frog, 16, 6, 4.5, 7)       # mouth
disc(frog, 12, 14, 1.2, 3)
disc(frog, 20, 14, 1.2, 3)

# 1D mapping: 32x32 sprite = 16 tiles, row-major
obj_tiles = to_tiles(ball, 8, 8)
obj_tiles += [0] * (8 * 3)      # pad so the frog starts at tile 4
obj_tiles += to_tiles(frog, 32, 32)
FROG_TILE = 4

# ---------------------------------------------------------------- font
FONT_CHARS = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ:!-"
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
}
font_tiles = []
for ch in FONT_CHARS:
    img = [[0] * 8 for _ in range(8)]
    rows = GLYPHS.get(ch, ". " * 7).split()
    for r, row in enumerate(rows):
        for c, p in enumerate(row):
            if p == "#":
                if img[r + 1][c + 2] == 0:
                    img[r + 1][c + 2] = 2
                img[r][c + 1] = 1
    font_tiles += to_tiles(img, 8, 8)

# sine table: 256 steps per turn, 1.12 fixed point
sin_tab = [int(round(math.sin(2 * math.pi * i / 256) * 4096)) for i in range(256)]


# ---------------------------------------------------------------- emit C
def c_array(ctype, name, vals, per_line=12, fmt="{}"):
    lines = []
    for i in range(0, len(vals), per_line):
        lines.append("    " + ", ".join(fmt.format(v) for v in vals[i:i + per_line]) + ",")
    return "const %s %s[%d] = {\n%s\n};\n" % (ctype, name, len(vals), "\n".join(lines))


hdr = f"""// Generated by tools/gen_assets.py - do not edit.
#ifndef ASSETS_H
#define ASSETS_H

#include <stdint.h>

#define PATH_LEN {len(PATH)}
#define FROG_X {CX}
#define FROG_Y {CY}
#define FROG_TILE {FROG_TILE}
#define NUM_COLORS {len(BALL_COLORS)}
#define FROG_PALBANK {len(BALL_COLORS)}
#define FONT_CHARS "{FONT_CHARS}"

extern const int16_t path_x[PATH_LEN];
extern const int16_t path_y[PATH_LEN];
extern const int16_t sin_tab[256];
extern const uint16_t bg_pal[{len(bg_pal)}];
extern const uint16_t obj_pal[{len(obj_pal)}];
extern const uint32_t bg_tiles[{len(bg_tiles)}];
extern const uint32_t obj_tiles[{len(obj_tiles)}];
extern const uint32_t font_tiles[{len(font_tiles)}];

#endif
"""

src = "// Generated by tools/gen_assets.py - do not edit.\n#include \"assets.h\"\n\n"
src += c_array("int16_t", "path_x", [p[0] for p in PATH], 16)
src += c_array("int16_t", "path_y", [p[1] for p in PATH], 16)
src += c_array("int16_t", "sin_tab", sin_tab, 16)
src += c_array("uint16_t", "bg_pal", bg_pal, 8, "0x{:04X}")
src += c_array("uint16_t", "obj_pal", obj_pal, 8, "0x{:04X}")
src += c_array("uint32_t", "bg_tiles", bg_tiles, 8, "0x{:08X}")
src += c_array("uint32_t", "obj_tiles", obj_tiles, 8, "0x{:08X}")
src += c_array("uint32_t", "font_tiles", font_tiles, 8, "0x{:08X}")

with open(os.path.join(ROOT, "include", "assets.h"), "w") as f:
    f.write(hdr)
with open(os.path.join(ROOT, "source", "assets.c"), "w") as f:
    f.write(src)


# ---------------------------------------------------------------- preview
def write_png(path, rows):
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw)))
        f.write(chunk(b"IEND", b""))


img = [[BG_PAL[bg[y][x]] for x in range(W)] for y in range(H)]


def blit(spr, sx, sy, pal):
    for y, row in enumerate(spr):
        for x, v in enumerate(row):
            if v and 0 <= sx + x < W and 0 <= sy + y < H:
                img[sy + y][sx + x] = pal[v]


blit(frog, CX - 16, CY - 16, FROG_PAL)
for i in range(30):
    px, py = PATH[40 + i * 8]
    blit(ball, px - 4, py - 4, [(0, 0, 0)] + BALL_COLORS[(i * 7 // 3) % 5])
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
scale = 3
big = [[img[y // scale][x // scale] for x in range(W * scale)] for y in range(H * scale)]
write_png(os.path.join(ROOT, "build", "preview.png"), big)
print(f"path length {len(PATH)} px, end {END}")
