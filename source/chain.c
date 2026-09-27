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

static int path_index(int32_t pos)
{
    int i = pos >> 8;
    if (i < 0) i = 0;
    if (i >= layout->len) i = layout->len - 1;
    return i;
}

void chain_point(int32_t pos, int *x, int *y)
{
    int i = path_index(pos);
    *x = layout->x[i];
    *y = layout->y[i];
}

int chain_angle(int32_t pos) { return layout->angle[path_index(pos)]; }

void chain_init(Chain *c, int total, int ncolors, uint32_t seed)
{
    c->count = 0;
    c->to_spawn = total;
    c->spawned = 0;
    c->ncolors = ncolors;
    c->pow_chance = 0;
    c->combo = 0;
    c->rng = seed ? seed : 0x1234567;
    chain_events_clear(c);
}

void chain_events_clear(Chain *c)
{
    c->triggered = 0;
    c->npopped = 0;
    c->nmatches = 0;
    c->contacts = 0;
}

int chain_danger(const Chain *c)
{
    if (c->count == 0) return 0;
    int32_t head = c->pos[c->count - 1] - (layout->hide << 8);
    int32_t span = (layout->len - layout->hide) << 8;
    if (head < 0) return 0;
    return head >= span ? 256 : (int)((int64_t)head * 256 / span);
}

static int touching(const Chain *c, int i)
{
    return c->pos[i] - c->pos[i - 1] <= BALL_D + 16;
}

static void insert_at(Chain *c, int k, int32_t pos, int color, int power)
{
    for (int i = c->count; i > k; i--) {
        c->pos[i] = c->pos[i - 1];
        c->color[i] = c->color[i - 1];
        c->power[i] = c->power[i - 1];
    }
    c->pos[k] = pos;
    c->color[k] = color;
    c->power[k] = power;
    c->count++;
}

// Take ball i out, noting it for the renderer and any power-up it carried.
static void pop_ball(Chain *c, int i)
{
    if (c->npopped < MAX_POPPED) {
        Popped *p = &c->popped[c->npopped++];
        int x, y;
        chain_point(c->pos[i], &x, &y);
        p->x = x;
        p->y = y;
        p->color = c->color[i];
        p->power = c->power[i];
    }
    if (c->power[i]) c->triggered |= 1u << c->power[i];
}

// Remove every ball with keep[i] == 0, keeping the order.
static void compact(Chain *c, const uint8_t *keep)
{
    int n = 0;
    for (int i = 0; i < c->count; i++) {
        if (!keep[i]) continue;
        c->pos[n] = c->pos[i];
        c->color[n] = c->color[i];
        c->power[n] = c->power[i];
        n++;
    }
    c->count = n;
}

// Push balls in front of `from` forward so none overlap.
static void push_from(Chain *c, int from)
{
    for (int i = from < 1 ? 1 : from; i < c->count; i++) {
        if (c->pos[i] < c->pos[i - 1] + BALL_D)
            c->pos[i] = c->pos[i - 1] + BALL_D;
    }
}

// Remove the same-colored touching run through ball k if it has 3 or more,
// plus anything caught in a bomb blast. Records a match event worth
// points_each x combo per ball. Returns the number of balls removed.
static int match_at(Chain *c, int k, int combo)
{
    if (k < 0 || k >= c->count) return 0;
    int col = c->color[k];
    int lo = k, hi = k;
    while (lo > 0 && c->color[lo - 1] == col && touching(c, lo)) lo--;
    while (hi + 1 < c->count && c->color[hi + 1] == col && touching(c, hi + 1)) hi++;
    if (hi - lo + 1 < 3) return 0;

    uint8_t keep[MAX_BALLS];
    int32_t bombs[8];
    int nbombs = 0;
    for (int i = 0; i < c->count; i++) keep[i] = i < lo || i > hi;
    for (int i = lo; i <= hi; i++)
        if (c->power[i] == POW_BOMB && nbombs < 8) bombs[nbombs++] = c->pos[i];
    for (int i = 0; i < c->count; i++) {
        for (int b = 0; b < nbombs && keep[i]; b++) {
            int32_t d = c->pos[i] - bombs[b];
            if (d < 0) d = -d;
            if (d <= BOMB_R) keep[i] = 0;
        }
    }

    int x, y, n = 0;
    chain_point(c->pos[k], &x, &y);
    for (int i = 0; i < c->count; i++)
        if (!keep[i]) {
            pop_ball(c, i);
            n++;
        }
    compact(c, keep);

    if (c->nmatches < MAX_MATCHES) {
        Match *m = &c->matches[c->nmatches++];
        m->x = x;
        m->y = y;
        m->combo = combo;
        m->points = n * 10 * combo;
    }
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
    int power = POW_NONE;
    if (c->pow_chance > 0 && c->spawned >= 8 && rand_next(&c->rng) % c->pow_chance == 0)
        power = 1 + rand_next(&c->rng) % (NUM_POWERS - 1);
    insert_at(c, 0, c->count > 0 ? c->pos[0] - BALL_D : 0, col, power);
    c->spawned++;
    if (c->to_spawn > 0) c->to_spawn--;
}

ChainState chain_update(Chain *c, int32_t speed, int *score)
{
    if (speed >= 0) {
        spawn(c);
        if (c->count > 0) {
            c->pos[0] += speed;
            push_from(c, 1);
        }
    } else if (c->count > 0) {
        // reverse: everything rolls back together until the tail reaches the start
        int32_t back = -speed;
        if (back > c->pos[0]) back = c->pos[0];
        for (int i = 0; i < c->count; i++) c->pos[i] -= back;
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
            c->contacts++;
            int n = match_at(c, i, c->combo + 1);
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
    int best = -1, best_d = HIT_R * HIT_R;
    int32_t hidden = layout->hide << 8;
    for (int i = 0; i < c->count; i++) {
        if (c->pos[i] < hidden) continue;
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
    int32_t side = (BALL_PX / 2) << 8;
    int after = dist2(p + side, x, y) < dist2(p - side, x, y);
    int k = after ? hit + 1 : hit;
    insert_at(c, k, after ? p + BALL_D : p, color, POW_NONE);
    push_from(c, k + 1);

    int n = match_at(c, k, 1);
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
