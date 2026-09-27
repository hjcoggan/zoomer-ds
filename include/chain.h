// Ball chain logic. Hardware independent so it can be tested on the host.
#ifndef CHAIN_H
#define CHAIN_H

#include <stdint.h>
#include "assets.h"

#define MAX_BALLS 160
#define BALL_D (BALL_PX << 8)     // ball spacing along the path, 8.8 fixed
#define RETRACT_SPEED (5 << 8)    // how fast a segment rolls back to a match
#define HIT_R 11                  // shot-to-ball distance that counts as a hit
#define BOMB_R (BALL_D * 7 / 2)   // a bomb clears balls this far along the track

enum { POW_NONE, POW_SLOW, POW_REVERSE, POW_BOMB, NUM_POWERS };

typedef enum { CHAIN_OK, CHAIN_LOST, CHAIN_CLEARED } ChainState;

// Something the renderer can show: a ball that popped, or a scoring match.
typedef struct { int16_t x, y; uint8_t color, power; } Popped;
typedef struct { int16_t x, y; int points, combo; } Match;
#define MAX_POPPED 96
#define MAX_MATCHES 4

typedef struct {
    int32_t pos[MAX_BALLS];      // distance along the path, 8.8 fixed; index 0 is the tail
    uint8_t color[MAX_BALLS];
    uint8_t power[MAX_BALLS];
    int count;
    int to_spawn;                // < 0 spawns forever
    int spawned;
    int ncolors;
    int pow_chance;              // 1 in this many new balls carries a power-up (0 = none)
    int combo;
    uint32_t rng;
    // events since the last chain_events_clear()
    unsigned triggered;          // bit per power-up that went off
    Popped popped[MAX_POPPED];
    int npopped;
    Match matches[MAX_MATCHES];
    int nmatches;
    int contacts;                // segments that rolled back and touched
} Chain;

uint32_t rand_next(uint32_t *state);

// Track the chain runs along; set before chain_init.
void chain_set_layout(const Layout *l);
const Layout *chain_layout(void);

void chain_init(Chain *c, int total, int ncolors, uint32_t seed);
void chain_events_clear(Chain *c);

// Advance one frame with the tail pushed at `speed` (8.8 px/frame).
// A negative speed rolls the whole chain backwards. Adds points from chain
// reactions to *score.
ChainState chain_update(Chain *c, int32_t speed, int *score);

// Index of the ball hit by a shot at pixel (x, y), or -1. Balls still inside
// the serpent can't be hit.
int chain_hit(const Chain *c, int x, int y);

// Insert a ball of `color` next to ball `hit`, resolve matches, return points.
int chain_insert(Chain *c, int hit, int x, int y, int color);

// Bitmask of colors currently in the chain.
unsigned chain_colors(const Chain *c);

// Path point and rolling direction for a position, clamped to the path.
void chain_point(int32_t pos, int *x, int *y);
int chain_angle(int32_t pos);

// How far along the track the front ball is, 0-256.
int chain_danger(const Chain *c);

#endif
