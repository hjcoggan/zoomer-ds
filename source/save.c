#include "save.h"

#define SRAM ((volatile uint8_t *)0x0E000000)
#define SAVE_MAGIC 0x414D555Au    // "ZUMA"
#define SAVE_VERSION 1

// Emulators and flash carts look for this string to pick the save type.
__attribute__((used, aligned(4))) const char save_type_tag[] = "SRAM_V113";

SaveData save;

typedef struct {
    uint32_t magic;
    uint32_t version;
    SaveData data;
    uint32_t checksum;
} SaveBlock;

static uint32_t checksum(const SaveBlock *b)
{
    const uint8_t *p = (const uint8_t *)b;
    uint32_t sum = 0x1234;
    for (unsigned i = 0; i < sizeof(SaveBlock) - sizeof(uint32_t); i++)
        sum = sum * 31 + p[i];
    return sum;
}

static void defaults(void)
{
    save.best_score = 0;
    save.best_level = 0;
    save.best_endless = 0;
    save.swap_aim = 0;
    save.swap_buttons = 0;
}

void save_load(void)
{
    // touch the tag so the linker can't discard it
    volatile const char *tag = save_type_tag;
    (void)tag[0];

    SaveBlock b;
    uint8_t *p = (uint8_t *)&b;
    // SRAM is on an 8-bit bus: byte reads only
    for (unsigned i = 0; i < sizeof(b); i++) p[i] = SRAM[i];
    if (b.magic != SAVE_MAGIC || b.version != SAVE_VERSION || b.checksum != checksum(&b)) {
        defaults();
        return;
    }
    save = b.data;
    save.swap_aim = save.swap_aim ? 1 : 0;
    save.swap_buttons = save.swap_buttons ? 1 : 0;
}

void save_write(void)
{
    SaveBlock b;
    const uint8_t *p = (const uint8_t *)&b;
    for (unsigned i = 0; i < sizeof(b); i++) ((uint8_t *)&b)[i] = 0;
    b.magic = SAVE_MAGIC;
    b.version = SAVE_VERSION;
    b.data = save;
    b.checksum = checksum(&b);
    for (unsigned i = 0; i < sizeof(b); i++) SRAM[i] = p[i];
}
