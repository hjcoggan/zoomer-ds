// High scores and settings, kept in a small file on the SD card (flash carts,
// or an emulator's DLDI SD image). If there is no card the game still runs, it
// just can't remember anything between sessions.
#ifndef SAVE_H
#define SAVE_H

#include <stdint.h>

typedef struct {
    int32_t best_score;       // adventure mode
    int32_t best_level;
    int32_t best_endless;
    uint8_t dpad_fast;        // 1 (default): d-pad spins fast, L/R fine; 0: the other way round
    uint8_t swap_buttons;     // 0: A shoots, B swaps; 1: B shoots, A swaps
    uint8_t guide;            // 1 (default): show the aim guide
    uint8_t pad;
} SaveData;

extern SaveData save;
extern int save_available;

void save_load(void);         // falls back to defaults if there's no file or it's corrupt
void save_write(void);

#endif
