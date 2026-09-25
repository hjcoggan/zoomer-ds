// Ball chain logic. Hardware independent so it can be tested on the host.
#ifndef CHAIN_H
#define CHAIN_H

#include <stdint.h>

#define MAX_BALLS 128
#define BALL_D (8 << 8)          // ball spacing along the path, 8.8 fixed
#define RETRACT_SPEED (3 << 8)   // how fast a segment rolls back to a match

typedef enum { CHAIN_OK, CHAIN_LOST, CHAIN_CLEARED } ChainState;

typedef struct {
    int32_t pos[MAX_BALLS];      // distance along the path, 8.8 fixed; index 0 is the tail
    uint8_t color[MAX_BALLS];
    int count;
    int to_spawn;
    int ncolors;
    int combo;
    uint32_t rng;
} Chain;

uint32_t rand_next(uint32_t *state);

void chain_init(Chain *c, int total, int ncolors, uint32_t seed);

// Advance one frame with the tail pushed at `speed` (8.8 px/frame).
// Adds any points earned from chain reactions to *score.
ChainState chain_update(Chain *c, int32_t speed, int *score);

// Index of the ball hit by a shot at pixel (x, y), or -1.
int chain_hit(const Chain *c, int x, int y);

// Insert a ball of `color` next to ball `hit`, resolve matches, return points.
int chain_insert(Chain *c, int hit, int x, int y, int color);

// Bitmask of colors currently in the chain.
unsigned chain_colors(const Chain *c);

// Path point for a position, clamped to the path.
void chain_point(int32_t pos, int *x, int *y);

#endif
