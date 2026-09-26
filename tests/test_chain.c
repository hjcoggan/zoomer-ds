#include <stdio.h>
#include <stdlib.h>
#include "chain.h"
#include "assets.h"

static int failures;

#define CHECK(cond) do { \
    if (!(cond)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); failures++; } \
} while (0)

static void set_chain(Chain *c, const char *colors, int32_t start)
{
    chain_init(c, 0, 4, 1);
    for (int i = 0; colors[i]; i++) {
        c->color[i] = colors[i] - '0';
        c->pos[i] = start + i * BALL_D;
    }
    c->count = 0;
    while (colors[c->count]) c->count++;
}

static int insert_next_to(Chain *c, int hit, int after, int color)
{
    int x, y;
    chain_point(c->pos[hit] + (after ? 5 << 8 : -(5 << 8)), &x, &y);
    return chain_insert(c, hit, x, y, color);
}

static void test_spawn_and_roll(void)
{
    Chain c;
    int score = 0;
    chain_init(&c, 20, 4, 42);
    for (int f = 0; f < 2000; f++) chain_update(&c, 256, &score);
    CHECK(c.count == 20);
    CHECK(c.to_spawn == 0);
    for (int i = 1; i < c.count; i++) CHECK(c.pos[i] - c.pos[i - 1] == BALL_D);
}

static void test_match_three(void)
{
    Chain c;
    set_chain(&c, "01120", 100 << 8);
    int pts = insert_next_to(&c, 2, 1, 1);   // 0 1 1 [1] 2 0
    CHECK(pts == 30);
    CHECK(c.count == 3);
    CHECK(c.color[0] == 0 && c.color[1] == 2 && c.color[2] == 0);
}

static void test_no_match_inserts(void)
{
    Chain c;
    set_chain(&c, "0123", 100 << 8);
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
    set_chain(&c, "22112", 100 << 8);
    score += insert_next_to(&c, 3, 1, 1);
    CHECK(c.count == 3);
    for (int f = 0; f < 60 && c.count; f++) chain_update(&c, 0, &score);
    CHECK(c.count == 0);
    CHECK(score == 30 + 30 * 2);
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
    for (int f = 0; f < 600; f++) chain_update(&c, 256, &score);
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
    }
    chain_set_layout(&layouts[0]);
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
    if (failures) {
        printf("%d failure(s)\n", failures);
        return 1;
    }
    printf("all chain tests passed\n");
    return 0;
}
