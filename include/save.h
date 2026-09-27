// High scores and settings, kept in cartridge SRAM.
#ifndef SAVE_H
#define SAVE_H

#include <stdint.h>

typedef struct {
    int32_t best_score;       // adventure mode
    int32_t best_level;
    int32_t best_endless;
    uint8_t dpad_fast;        // 1 (default): d-pad spins fast, L/R fine; 0: the other way round
    uint8_t swap_buttons;     // 0: A shoots, B swaps; 1: B shoots, A swaps
} SaveData;

extern SaveData save;

void save_load(void);         // falls back to defaults if SRAM is blank or corrupt
void save_write(void);

#endif
