#include <nds.h>
#include <gl2d.h>
#include "scene.h"
#include "assets.h"

#define WHITE RGB15(31, 31, 31)
#define MAX_PARTICLES 220
#define MAX_POPUPS 8

// glLoadTileSet fills in one glImage for every tile the texture can hold,
// used or not, so these are sized by texture size / tile size.
static glImage img_balls[(128 / 16) * (128 / 16)];
static glImage img_frog[(128 / 64) * (64 / 64)];
static glImage img_font[(128 / 8) * (8 / 8)];
static glImage img_ring[1];
_Static_assert(NUM_BALL_TILES <= (128 / 16) * (128 / 16), "ball texture full");
static int ox, oy;              // screen shake
static int poly_id;

void scene_init(void)
{
    glScreen2D();
    glEnable(GL_BLEND);
    glEnable(GL_ANTIALIAS);
    glClearColor(0, 0, 0, 0);    // transparent, so the level picture shows through
    glClearPolyID(63);
    const int param = GL_TEXTURE_COLOR0_TRANSPARENT | TEXGEN_OFF;
    glLoadTileSet(img_balls, 16, 16, 128, 128, GL_RGB256, TEXTURE_SIZE_128, TEXTURE_SIZE_128,
                  param, 256, tex_balls_pal, tex_balls);
    glLoadTileSet(img_frog, 64, 64, 128, 64, GL_RGB256, TEXTURE_SIZE_128, TEXTURE_SIZE_64,
                  param, 256, tex_frog_pal, tex_frog);
    glLoadTileSet(img_font, 8, 8, 128, 8, GL_RGB256, TEXTURE_SIZE_128, TEXTURE_SIZE_8,
                  param, 256, tex_font_pal, tex_font);
    glLoadTileSet(img_ring, 32, 32, 32, 32, GL_RGB256, TEXTURE_SIZE_32, TEXTURE_SIZE_32,
                  param, 256, tex_ring_pal, tex_ring);
}

// Translucent polygons only blend over ones with a different ID, so effects
// cycle through IDs.
static void fmt(int alpha)
{
    if (alpha >= 31) {
        glPolyFmt(POLY_ALPHA(31) | POLY_CULL_NONE | POLY_ID(0));
        return;
    }
    if (alpha < 1) alpha = 1;
    poly_id = poly_id % 60 + 1;
    glPolyFmt(POLY_ALPHA(alpha) | POLY_CULL_NONE | POLY_ID(poly_id));
}

void scene_begin(int shake_x, int shake_y)
{
    ox = shake_x;
    oy = shake_y;
    glBegin2D();
    glColor(WHITE);
    fmt(31);
}

void scene_end(void)
{
    glColor(WHITE);
    glEnd2D();
    glFlush(0);
}

void draw_ball(int x, int y, int color, int frame, int angle, int power, int scale)
{
    x += ox;
    y += oy;
    if (scale == 4096) {
        glPolyFmt(POLY_ALPHA(9) | POLY_CULL_NONE | POLY_ID(61));   // soft shadow
        glSprite(x - 6, y - 6, GL_FLIP_NONE, &img_balls[TEX_SHADOW]);
    }
    fmt(31);
    glSpriteRotateScale(x, y, angle, scale, GL_FLIP_NONE, &img_balls[TEX_BALL(color, frame & (BALL_FRAMES - 1))]);
    glPolyFmt(POLY_ALPHA(22) | POLY_CULL_NONE | POLY_ID(62));
    glSpriteScale(x - 8 * scale / 4096, y - 8 * scale / 4096, scale, GL_FLIP_NONE, &img_balls[TEX_HIGHLIGHT]);
    if (power) {
        fmt(31);
        glSpriteScale(x - 8 * scale / 4096, y - 8 * scale / 4096, scale, GL_FLIP_NONE, &img_balls[TEX_POW(power)]);
        int pulse = (int)((frame * 3) & 15);
        fmt(8 + (pulse < 8 ? pulse : 15 - pulse) * 2);
        glColor(RGB15(31, 28, 12));
        glSpriteRotateScale(x, y, 0, scale * 9 / 8, GL_FLIP_NONE, &img_balls[TEX_GLOW]);
        glColor(WHITE);
    }
}

void draw_frog(int x, int y, int angle, int blink)
{
    fmt(31);
    glSpriteRotate(x + ox, y + oy, angle, GL_FLIP_NONE, &img_frog[blink ? 1 : 0]);
}

void draw_dot(int x, int y, uint16_t rgb, int alpha)
{
    fmt(alpha);
    glColor(rgb);
    glSprite(x + ox - 8, y + oy - 8, GL_FLIP_NONE, &img_balls[TEX_DOT]);
    glColor(WHITE);
}

// ---------------------------------------------------------------- particles

enum { P_SPARK, P_STAR, P_RING, P_BIGRING };

typedef struct {
    int32_t x, y, vx, vy;        // 8.8
    int16_t life, max;
    uint16_t rgb;
    uint8_t kind, size;
    int16_t spin;
} Particle;

typedef struct {
    int32_t x, y;                // 8.8
    int life, points, combo;
} Popup;

static Particle parts[MAX_PARTICLES];
static int nparts;
static Popup popups[MAX_POPUPS];
static uint32_t seed = 0xBEEF;

static int rnd(int n)
{
    seed = seed * 1664525u + 1013904223u;
    return (int)((seed >> 8) % (uint32_t)n);
}

static Particle *new_particle(void)
{
    if (nparts < MAX_PARTICLES) return &parts[nparts++];
    return &parts[rnd(MAX_PARTICLES)];         // full: recycle one at random
}

void fx_clear(void)
{
    nparts = 0;
    for (int i = 0; i < MAX_POPUPS; i++) popups[i].life = 0;
}

void fx_burst(int x, int y, uint16_t rgb, int n)
{
    for (int i = 0; i < n; i++) {
        Particle *p = new_particle();
        int a = rnd(32768);
        int sp = 200 + rnd(420);                 // 8.8 px per frame
        p->x = x << 8;
        p->y = y << 8;
        p->vx = (cosLerp(a) * sp) >> 12;
        p->vy = (sinLerp(a) * sp) >> 12;
        p->max = p->life = 18 + rnd(16);
        p->rgb = rgb;
        p->kind = P_SPARK;
        p->size = 2 + rnd(3);
        p->spin = 0;
    }
}

void fx_stars(int x, int y, int n)
{
    for (int i = 0; i < n; i++) {
        Particle *p = new_particle();
        int a = rnd(32768);
        int sp = 120 + rnd(300);
        p->x = x << 8;
        p->y = y << 8;
        p->vx = (cosLerp(a) * sp) >> 12;
        p->vy = ((sinLerp(a) * sp) >> 12) - 180;
        p->max = p->life = 30 + rnd(20);
        p->rgb = RGB15(31, 27, 8);
        p->kind = P_STAR;
        p->size = 3 + rnd(3);
        p->spin = rnd(2) ? 600 : -600;
    }
}

void fx_ring(int x, int y, uint16_t rgb, int big)
{
    Particle *p = new_particle();
    p->x = x << 8;
    p->y = y << 8;
    p->vx = p->vy = 0;
    p->max = p->life = big ? 34 : 20;
    p->rgb = rgb;
    p->kind = big ? P_BIGRING : P_RING;
    p->size = 0;
    p->spin = 0;
}

void fx_popup(int x, int y, int points, int combo)
{
    Popup *best = &popups[0];
    for (int i = 0; i < MAX_POPUPS; i++)
        if (popups[i].life < best->life) best = &popups[i];
    best->x = x << 8;
    best->y = y << 8;
    best->life = 60;
    best->points = points;
    best->combo = combo;
}

void fx_update(void)
{
    for (int i = 0; i < nparts;) {
        Particle *p = &parts[i];
        if (--p->life <= 0) {
            *p = parts[--nparts];
            continue;
        }
        p->x += p->vx;
        p->y += p->vy;
        if (p->kind == P_SPARK) {
            p->vx = p->vx * 15 / 16;
            p->vy = p->vy * 15 / 16 + 6;          // a little gravity
        } else if (p->kind == P_STAR) {
            p->vy += 10;
        }
        i++;
    }
    for (int i = 0; i < MAX_POPUPS; i++)
        if (popups[i].life > 0) {
            popups[i].life--;
            popups[i].y -= 90;
        }
}

static void draw_text(int x, int y, const char *s, uint16_t rgb, int alpha)
{
    fmt(alpha);
    glColor(rgb);
    for (; *s; s++, x += 7) {
        const char *f = POPUP_CHARS;
        int t = 0;
        while (f[t] && f[t] != *s) t++;
        if (f[t]) glSprite(x, y, GL_FLIP_NONE, &img_font[t]);
    }
    glColor(WHITE);
}

void fx_draw(void)
{
    for (int i = 0; i < nparts; i++) {
        Particle *p = &parts[i];
        int x = (p->x >> 8) + ox, y = (p->y >> 8) + oy;
        int alpha = 4 + 27 * p->life / p->max;
        glColor(p->rgb);
        switch (p->kind) {
        case P_SPARK:
            fmt(alpha);
            glSpriteRotateScale(x, y, 0, p->size * 4096 / 6, GL_FLIP_NONE, &img_balls[TEX_SPARK]);
            break;
        case P_STAR:
            fmt(alpha);
            glSpriteRotateScale(x, y, p->spin * p->life, p->size * 4096 / 6, GL_FLIP_NONE, &img_balls[TEX_STAR]);
            break;
        case P_RING:
        case P_BIGRING: {
            int t = p->max - p->life;
            int s = (p->kind == P_BIGRING ? 1400 : 900) + t * (p->kind == P_BIGRING ? 420 : 260);
            fmt(2 + 22 * p->life / p->max);
            glSpriteRotateScale(x, y, 0, s, GL_FLIP_NONE, &img_ring[0]);
            break;
        }
        }
    }
    glColor(WHITE);
    for (int i = 0; i < MAX_POPUPS; i++) {
        Popup *q = &popups[i];
        if (q->life <= 0) continue;
        char buf[16], *p = buf;
        *p++ = '+';
        char tmp[12];
        int n = 0, v = q->points;
        do {
            tmp[n++] = '0' + v % 10;
            v /= 10;
        } while (v);
        while (n) *p++ = tmp[--n];
        if (q->combo > 1) {
            *p++ = 'X';
            *p++ = '0' + (q->combo > 9 ? 9 : q->combo);
        }
        *p = 0;
        int w = (p - buf) * 7;
        int alpha = q->life > 20 ? 31 : 4 + q->life * 27 / 20;
        draw_text((q->x >> 8) - w / 2 + ox, (q->y >> 8) - 4 + oy, buf,
                  q->combo > 1 ? RGB15(31, 20, 6) : RGB15(31, 29, 12), alpha);
    }
}
