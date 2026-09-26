// Minimal GBA hardware definitions.
#ifndef GBA_H
#define GBA_H

#include <stdint.h>

#define REG_DISPCNT  (*(volatile uint16_t *)0x04000000)
#define REG_VCOUNT   (*(volatile uint16_t *)0x04000006)
#define REG_BG0CNT   (*(volatile uint16_t *)0x04000008)
#define REG_BG1CNT   (*(volatile uint16_t *)0x0400000A)
#define REG_BG1VOFS  (*(volatile uint16_t *)0x04000016)
#define REG_KEYINPUT (*(volatile uint16_t *)0x04000130)
#define REG_BLDCNT   (*(volatile uint16_t *)0x04000050)
#define REG_BLDY     (*(volatile uint16_t *)0x04000054)

#define DCNT_MODE0  0x0000
#define DCNT_OBJ_1D 0x0040
#define DCNT_BG0    0x0100
#define DCNT_BG1    0x0200
#define DCNT_OBJ    0x1000

#define BG_PRIO(n)  (n)
#define BG_CBB(n)   ((n) << 2)
#define BG_SBB(n)   ((n) << 8)
#define BG_8BPP     0x0080

// brightness fade: targets BG0-3, OBJ, backdrop
#define BLD_BG0     0x01
#define BLD_BG1     0x02
#define BLD_OBJ     0x10
#define BLD_BD      0x20
#define BLD_DARKEN  0x00C0

#define PAL_BG   ((volatile uint16_t *)0x05000000)
#define PAL_OBJ  ((volatile uint16_t *)0x05000200)
#define VRAM     ((volatile uint16_t *)0x06000000)
#define CHARBLOCK(n)  ((volatile uint32_t *)(0x06000000 + (n) * 0x4000))
#define SCREENBLOCK(n) ((volatile uint16_t *)(0x06000000 + (n) * 0x800))
#define OBJ_TILES ((volatile uint32_t *)0x06010000)
#define OAM      ((volatile uint16_t *)0x07000000)

#define KEY_A      0x0001
#define KEY_B      0x0002
#define KEY_SELECT 0x0004
#define KEY_START  0x0008
#define KEY_RIGHT  0x0010
#define KEY_LEFT   0x0020
#define KEY_UP     0x0040
#define KEY_DOWN   0x0080
#define KEY_R      0x0100
#define KEY_L      0x0200

#define ATTR0_AFFINE 0x0100
#define ATTR0_HIDE   0x0200
#define ATTR1_SIZE8  0x0000
#define ATTR1_SIZE32 0x8000
#define ATTR2_PRIO(n) ((n) << 10)
#define ATTR2_PAL(n)  ((n) << 12)

static inline void vsync(void)
{
    while (REG_VCOUNT >= 160) {}
    while (REG_VCOUNT < 160) {}
}

#endif
