#include "chain.h"

static const Layout *layout = &layouts[0];

void chain_set_layout(const Layout *l) { layout = l; }
const Layout *chain_layout(void) { return layout; }

uint32_t rand_next(uint32_t *s)
{
    uint32_t x = *s;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return *s = x;
}

void chain_point(int32_t pos, int *x, int *y)
{
    int i = pos >> 8;
    if (i < 0) i = 0;
    if (i >= layout->len) i = layout->len - 1;
    *x = layout->x[i];
    *y = layout->y[i];
}

void chain_init(Chain *c, int total, int ncolors, uint32_t seed)
{
    c->count = 0;
    c->to_spawn = total;
    c->ncolors = ncolors;
    c->combo = 0;
    c->rng = seed ? seed : 0x1234567;
}

static int touching(const Chain *c, int i)
{
    return c->pos[i] - c->pos[i - 1] <= BALL_D + 16;
}

static void insert_at(Chain *c, int k, int32_t pos, int color)
{
    for (int i = c->count; i > k; i--) {
        c->pos[i] = c->pos[i - 1];
        c->color[i] = c->color[i - 1];
    }
    c->pos[k] = pos;
    c->color[k] = color;
    c->count++;
}

static void remove_range(Chain *c, int first, int n)
{
    for (int i = first; i + n < c->count; i++) {
        c->pos[i] = c->pos[i + n];
        c->color[i] = c->color[i + n];
    }
    c->count -= n;
}

// Push balls in front of `from` forward so none overlap.
static void push_from(Chain *c, int from)
{
    for (int i = from < 1 ? 1 : from; i < c->count; i++) {
        if (c->pos[i] < c->pos[i - 1] + BALL_D)
            c->pos[i] = c->pos[i - 1] + BALL_D;
    }
}

// Remove the same-colored touching run through ball k if it has 3 or more.
// Returns the number of balls removed.
static int match_at(Chain *c, int k)
{
    if (k < 0 || k >= c->count) return 0;
    int col = c->color[k];
    int lo = k, hi = k;
    while (lo > 0 && c->color[lo - 1] == col && touching(c, lo)) lo--;
    while (hi + 1 < c->count && c->color[hi + 1] == col && touching(c, hi + 1)) hi++;
    int n = hi - lo + 1;
    if (n < 3) return 0;
    remove_range(c, lo, n);
    return n;
}

static void spawn(Chain *c)
{
    if (c->to_spawn == 0 || c->count >= MAX_BALLS - 1) return;
    if (c->count > 0 && c->pos[0] < BALL_D) return;
    int col;
    if (c->count > 0 && (rand_next(&c->rng) % 3) == 0)
        col = c->color[0];
    else
        col = rand_next(&c->rng) % c->ncolors;
    insert_at(c, 0, c->count > 0 ? c->pos[0] - BALL_D : 0, col);
    if (c->to_spawn > 0) c->to_spawn--;
}

ChainState chain_update(Chain *c, int32_t speed, int *score)
{
    spawn(c);

    if (c->count > 0) {
        c->pos[0] += speed;
        push_from(c, 1);
    }

    // A segment whose rear ball matches the ball behind the gap rolls back.
    for (int i = 1; i < c->count; i++) {
        if (c->pos[i] - c->pos[i - 1] <= BALL_D || c->color[i] != c->color[i - 1])
            continue;
        int end = i + 1;
        while (end < c->count && touching(c, end)) end++;
        int32_t gap = c->pos[i] - (c->pos[i - 1] + BALL_D);
        int32_t move = gap < RETRACT_SPEED ? gap : RETRACT_SPEED;
        for (int j = i; j < end; j++) c->pos[j] -= move;
        if (move == gap) {
            int n = match_at(c, i);
            if (n) {
                c->combo++;
                *score += n * 10 * c->combo;
            } else {
                c->combo = 0;
            }
        }
        break;
    }

    if (c->count > 0 && (c->pos[c->count - 1] >> 8) >= layout->len - 1)
        return CHAIN_LOST;
    if (c->count == 0 && c->to_spawn == 0)
        return CHAIN_CLEARED;
    return CHAIN_OK;
}

int chain_hit(const Chain *c, int x, int y)
{
    int best = -1, best_d = 7 * 7;
    for (int i = 0; i < c->count; i++) {
        int px, py;
        chain_point(c->pos[i], &px, &py);
        int dx = px - x, dy = py - y;
        int d = dx * dx + dy * dy;
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    return best;
}

static int dist2(int32_t pos, int x, int y)
{
    int px, py;
    chain_point(pos, &px, &py);
    return (px - x) * (px - x) + (py - y) * (py - y);
}

int chain_insert(Chain *c, int hit, int x, int y, int color)
{
    if (c->count >= MAX_BALLS - 1) return 0;
    int32_t p = c->pos[hit];
    int after = dist2(p + (4 << 8), x, y) < dist2(p - (4 << 8), x, y);
    int k = after ? hit + 1 : hit;
    insert_at(c, k, after ? p + BALL_D : p, color);
    push_from(c, k + 1);

    int n = match_at(c, k);
    if (!n) {
        c->combo = 0;
        return 0;
    }
    c->combo = 1;
    return n * 10;
}

unsigned chain_colors(const Chain *c)
{
    unsigned m = 0;
    for (int i = 0; i < c->count; i++) m |= 1u << c->color[i];
    return m;
}
