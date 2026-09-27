#include <stdio.h>
#include <stdlib.h>
#include "chain.h"
#include "assets.h"

static int failures;

#define CHECK(cond) do { \
    if (!(cond)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); failures++; } \
} while (0)

// Balls touching each other, starting well past the serpent's mouth.
static void set_chain(Chain *c, const char *colors, int32_t start)
{
    chain_init(c, 0, 4, 1);
    for (int i = 0; colors[i]; i++) {
        c->color[i] = colors[i] - '0';
        c->power[i] = POW_NONE;
        c->pos[i] = start + i * BALL_D;
    }
    c->count = 0;
    while (colors[c->count]) c->count++;
}

static int32_t visible(int px) { return (chain_layout()->hide + px) << 8; }

static int insert_next_to(Chain *c, int hit, int after, int color)
{
    int x, y;
    int32_t off = (BALL_PX / 2 + 1) << 8;
    chain_point(c->pos[hit] + (after ? off : -off), &x, &y);
    return chain_insert(c, hit, x, y, color);
}

static void test_spawn_and_roll(void)
{
    Chain c;
    int score = 0;
    chain_init(&c, 20, 4, 42);
    for (int f = 0; f < 3000; f++) chain_update(&c, 256, &score);
    CHECK(c.count == 20);
    CHECK(c.to_spawn == 0);
    for (int i = 1; i < c.count; i++) CHECK(c.pos[i] - c.pos[i - 1] == BALL_D);
}

static void test_match_three(void)
{
    Chain c;
    set_chain(&c, "01120", visible(100));
    int pts = insert_next_to(&c, 2, 1, 1);   // 0 1 1 [1] 2 0
    CHECK(pts == 30);
    CHECK(c.count == 3);
    CHECK(c.color[0] == 0 && c.color[1] == 2 && c.color[2] == 0);
    CHECK(c.npopped == 3);
    CHECK(c.nmatches == 1 && c.matches[0].points == 30);
}

static void test_no_match_inserts(void)
{
    Chain c;
    set_chain(&c, "0123", visible(100));
    int pts = insert_next_to(&c, 1, 0, 3);
    CHECK(pts == 0);
    CHECK(c.count == 5);
    CHECK(c.color[1] == 3 && c.color[2] == 1);
    for (int i = 1; i < c.count; i++) CHECK(c.pos[i] - c.pos[i - 1] >= BALL_D);
}

static void test_retract_combo(void)
{
    Chain c;
    int score = 0;
    // 2 2 | 1 1 [1] | 2 : clearing the 1s leaves a gap with 2s on both sides
    set_chain(&c, "22112", visible(100));
    score += insert_next_to(&c, 3, 1, 1);
    CHECK(c.count == 3);
    for (int f = 0; f < 60 && c.count; f++) chain_update(&c, 0, &score);
    CHECK(c.count == 0);
    CHECK(score == 30 + 30 * 2);
    CHECK(c.contacts == 1);
}

static void test_lose(void)
{
    Chain c;
    int score = 0;
    set_chain(&c, "0", (chain_layout()->len - 2) << 8);
    ChainState s = CHAIN_OK;
    for (int f = 0; f < 10 && s == CHAIN_OK; f++) s = chain_update(&c, 256, &score);
    CHECK(s == CHAIN_LOST);
}

static void test_endless_keeps_spawning(void)
{
    Chain c;
    int score = 0;
    chain_init(&c, -1, 4, 7);
    for (int f = 0; f < 700; f++) chain_update(&c, 256, &score);
    CHECK(c.count > 40);
    CHECK(c.to_spawn < 0);
}

static void test_every_layout(void)
{
    for (int l = 0; l < NUM_LAYOUTS; l++) {
        Chain c;
        int score = 0;
        chain_set_layout(&layouts[l]);
        chain_init(&c, 30, 3, 99);
        ChainState s = CHAIN_OK;
        int f;
        for (f = 0; f < 20000 && s == CHAIN_OK; f++) s = chain_update(&c, 128, &score);
        CHECK(s == CHAIN_LOST);           // an unattended chain reaches the hole
        CHECK(f > layouts[l].len / 2);
        CHECK(chain_danger(&c) >= 250);
    }
    chain_set_layout(&layouts[0]);
}

static void test_bomb_clears_neighbours(void)
{
    Chain c;
    // 0 0 1 1 [B1] 2 3 3 0 : matching the bomb also takes out balls within 3.5 on each side
    set_chain(&c, "001112330", visible(100));
    c.power[3] = POW_BOMB;
    c.count = 9;
    // remove index 4 so there are only two 1s plus the bomb 1 before we shoot
    for (int i = 4; i < 8; i++) {
        c.color[i] = c.color[i + 1];
        c.power[i] = c.power[i + 1];
    }
    c.count = 8;                          // 0 0 1 B1 2 3 3 0
    int pts = insert_next_to(&c, 3, 1, 1);  // 0 0 1 B1 [1] 2 3 3 0
    CHECK(pts > 30);
    CHECK(c.triggered & (1u << POW_BOMB));
    CHECK(c.count < 9 - 3);
}

static void test_powers_trigger(void)
{
    Chain c;
    set_chain(&c, "0112", visible(100));
    c.power[1] = POW_SLOW;
    insert_next_to(&c, 2, 1, 1);
    CHECK(c.triggered & (1u << POW_SLOW));
    CHECK(!(c.triggered & (1u << POW_REVERSE)));
}

static void test_reverse_rolls_back(void)
{
    Chain c;
    int score = 0;
    set_chain(&c, "0123", visible(100));
    int32_t before = c.pos[3];
    chain_update(&c, -512, &score);
    CHECK(c.pos[3] == before - 512);
    // can't go back past the start of the track
    for (int f = 0; f < 2000; f++) chain_update(&c, -512, &score);
    CHECK(c.pos[0] == 0);
    CHECK(c.pos[1] - c.pos[0] == BALL_D);
}

static void test_hidden_balls_cannot_be_hit(void)
{
    Chain c;
    set_chain(&c, "0", 4 << 8);
    int x, y;
    chain_point(c.pos[0], &x, &y);
    CHECK(chain_hit(&c, x, y) == -1);
    c.pos[0] = visible(20);
    chain_point(c.pos[0], &x, &y);
    CHECK(chain_hit(&c, x, y) == 0);
}

static void test_power_ups_spawn(void)
{
    Chain c;
    int score = 0;
    chain_init(&c, 200, 4, 5);
    c.pow_chance = 10;
    int seen = 0;
    for (int f = 0; f < 4000 && c.count < MAX_BALLS - 2; f++) {
        chain_update(&c, 256, &score);
        if (chain_danger(&c) > 200) break;
    }
    for (int i = 0; i < c.count; i++) seen |= c.power[i] != POW_NONE;
    CHECK(seen);
}

int main(void)
{
    test_spawn_and_roll();
    test_match_three();
    test_no_match_inserts();
    test_retract_combo();
    test_lose();
    test_endless_keeps_spawning();
    test_every_layout();
    test_bomb_clears_neighbours();
    test_powers_trigger();
    test_reverse_rolls_back();
    test_hidden_balls_cannot_be_hit();
    test_power_ups_spawn();
    if (failures) {
        printf("%d failure(s)\n", failures);
        return 1;
    }
    printf("all chain tests passed\n");
    return 0;
}
