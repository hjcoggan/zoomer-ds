#include <stdint.h>
#include "sound.h"

#define REG16(a) (*(volatile uint16_t *)(a))
#define SND1_SWEEP REG16(0x04000060)
#define SND1_CNT   REG16(0x04000062)
#define SND1_FREQ  REG16(0x04000064)
#define SND2_CNT   REG16(0x04000068)
#define SND2_FREQ  REG16(0x0400006C)
#define SND3_SEL   REG16(0x04000070)
#define SND3_CNT   REG16(0x04000072)
#define SND3_FREQ  REG16(0x04000074)
#define SND4_CNT   REG16(0x04000078)
#define SND4_FREQ  REG16(0x0400007C)
#define SND_DMGCNT REG16(0x04000080)
#define SND_DSCNT  REG16(0x04000082)
#define SND_STAT   REG16(0x04000084)
#define WAVE_RAM   ((volatile uint16_t *)0x04000090)

#define RESTART 0x8000
#define LEN_ON  0x4000

// envelope: initial volume, step time (0 = hold), direction
#define ENV(vol, step, up) (((vol) << 12) | ((up) << 11) | ((step) << 8))
#define DUTY(d) ((d) << 6)

// ---------------------------------------------------------------- the song
// A minor pentatonic jungle groove, 8 bars of 16 steps.
// Tokens: note like A4 or C#5, "--" holds the previous note, ".." is silence.
#define STEPS_PER_BAR 16
#define BARS 8
#define STEP_FRAMES 7                 // ~128 BPM in 16th notes

static const char *const lead_src[BARS] = {
    "A4 -- C5 -- D5 -- E5 -- G5 -- E5 -- D5 -- C5 --",
    "D5 -- E5 -- A4 -- -- -- C5 D5 C5 -- A4 -- -- ..",
    "E5 -- G5 -- A5 -- G5 E5 D5 -- E5 -- G5 -- -- --",
    "A5 -- G5 -- E5 -- D5 -- C5 -- D5 -- A4 -- -- ..",
    "C5 C5 .. C5 D5 -- E5 -- G5 G5 .. G5 A5 -- G5 --",
    "E5 -- D5 -- C5 -- D5 -- E5 -- -- -- .. .. .. ..",
    "C5 C5 .. C5 D5 -- E5 -- G5 G5 .. G5 A5 -- C6 --",
    "A5 -- G5 -- E5 -- D5 -- E5 -- -- -- A4 -- -- ..",
};

static const char *const bass_src[BARS] = {
    "A2 -- .. A2 A3 -- A2 -- A2 -- .. A2 G2 -- G2 --",
    "F2 -- .. F2 F3 -- F2 -- G2 -- .. G2 E2 -- E2 --",
    "C3 -- .. C3 C3 -- G2 -- C3 -- .. C3 G2 -- E2 --",
    "F2 -- .. F2 G2 -- G2 -- A2 -- .. A2 A2 -- .. ..",
    "A2 -- .. A2 A3 -- A2 -- E2 -- .. E2 E3 -- E2 --",
    "F2 -- .. F2 F3 -- F2 -- G2 -- .. G2 G3 -- G2 --",
    "A2 -- .. A2 A3 -- A2 -- E2 -- .. E2 E3 -- E2 --",
    "F2 -- .. F2 G2 -- G2 -- A2 -- A2 -- A2 -- .. ..",
};

// K kick, S snare, H hi-hat, T low tom, - nothing
static const char *const drum_src[BARS] = {
    "K-H-S-HKK-H-S-HH",
    "K-H-S-HKK-H-S-HH",
    "K-H-S-HKK-H-S-HH",
    "K-H-S-HKK-H-STTT",
    "K-H-S-HKK-H-S-HH",
    "K-H-S-HKK-H-S-HH",
    "K-H-S-HKK-H-S-HH",
    "K-H-S-HKS-SSTTTT",
};

#define HOLD 1
#define REST 0
#define SONG_STEPS (BARS * STEPS_PER_BAR)

static uint8_t lead[SONG_STEPS], bass[SONG_STEPS];
static char drums[SONG_STEPS];

// ---------------------------------------------------------------- pitch
// 16x the frequency of C8..B8 (MIDI 108-119); lower octaves shift right.
static const uint32_t top_octave_x16[12] = {
    66976, 70959, 75178, 79648, 84385, 89402,
    94719, 100351, 106318, 112640, 119338, 126434,
};
static uint16_t square_rate[128], wave_rate[128];

static void build_rates(void)
{
    for (int n = 24; n < 108; n++) {
        uint32_t f16 = top_octave_x16[n % 12] >> (9 - n / 12);
        int sq = 2048 - (int)(2097152u / f16);   // 131072 Hz / f
        int wv = 2048 - (int)(1048576u / f16);   // 65536 Hz / f
        square_rate[n] = sq < 0 ? 0 : sq;
        wave_rate[n] = wv < 0 ? 0 : wv;
    }
}

static void parse_notes(const char *const *src, uint8_t *out)
{
    static const int8_t semis[7] = { 9, 11, 0, 2, 4, 5, 7 };   // A..G
    int k = 0;
    for (int bar = 0; bar < BARS; bar++) {
        const char *s = src[bar];
        for (int step = 0; step < STEPS_PER_BAR; step++) {
            while (*s == ' ') s++;
            if (s[0] == '-') {
                out[k] = HOLD;
                s += 2;
            } else if (s[0] == '.') {
                out[k] = REST;
                s += 2;
            } else {
                int n = semis[s[0] - 'A'];
                s++;
                if (*s == '#') {
                    n++;
                    s++;
                }
                out[k] = (uint8_t)((*s - '0' + 1) * 12 + n);
                s++;
            }
            k++;
        }
    }
}

// ---------------------------------------------------------------- state
static int music_on, step, step_timer;

// sound effect sequencer for ch1: pairs of (midi note, frames), 0-terminated
static const uint8_t *sfx_seq;
static int sfx_timer;
static uint8_t sfx_buf[16];

static void wave_init(void)
{
    // soft triangle for the bass, upper nibble plays first
    static const uint8_t tri[16] = {
        0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF,
        0xFE, 0xDC, 0xBA, 0x98, 0x76, 0x54, 0x32, 0x10,
    };
    SND3_SEL = 0x40;      // play bank 1 so the CPU writes bank 0
    for (int i = 0; i < 8; i++) WAVE_RAM[i] = tri[i * 2] | (tri[i * 2 + 1] << 8);
    SND3_SEL = 0x80;      // enable, play bank 0
}

void sound_init(void)
{
    SND_STAT = 0x80;              // master enable
    SND_DMGCNT = 0xFF77;          // all 4 channels, both speakers, full volume
    SND_DSCNT = 0x0002;           // PSG at 100%
    SND1_SWEEP = 0x0008;
    wave_init();
    build_rates();
    parse_notes(lead_src, lead);
    parse_notes(bass_src, bass);
    for (int bar = 0; bar < BARS; bar++)
        for (int i = 0; i < STEPS_PER_BAR; i++)
            drums[bar * STEPS_PER_BAR + i] = drum_src[bar][i];
}

static void play_drum(char d)
{
    switch (d) {
    case 'K':
        SND4_CNT = ENV(13, 1, 0);
        SND4_FREQ = RESTART | (7 << 4) | 3;
        break;
    case 'S':
        SND4_CNT = ENV(10, 1, 0);
        SND4_FREQ = RESTART | (3 << 4) | 1;
        break;
    case 'H':
        SND4_CNT = ENV(5, 1, 0) | 56;
        SND4_FREQ = RESTART | LEN_ON | (0 << 4) | 0;
        break;
    case 'T':
        SND4_CNT = ENV(11, 2, 0);
        SND4_FREQ = RESTART | 0x08 | (5 << 4) | 2;
        break;
    }
}

static void music_step(void)
{
    uint8_t n = lead[step];
    if (n == REST) {
        SND2_CNT = 0;
        SND2_FREQ = RESTART;
    } else if (n != HOLD) {
        SND2_CNT = DUTY(2) | ENV(10, 3, 0);
        SND2_FREQ = RESTART | square_rate[n];
    }

    n = bass[step];
    if (n == REST) {
        SND3_CNT = 0;
    } else if (n != HOLD) {
        SND3_CNT = 1 << 13;       // 100% volume
        SND3_FREQ = RESTART | wave_rate[n];
    }

    play_drum(drums[step]);
}

void music_play(void)
{
    music_on = 1;
    step = 0;
    step_timer = 0;
}

void music_stop(void)
{
    music_on = 0;
    SND2_CNT = 0;
    SND2_FREQ = RESTART;
    SND3_CNT = 0;
    SND4_CNT = 0;
    SND4_FREQ = RESTART;
}

void sound_update(void)
{
    if (music_on && --step_timer <= 0) {
        step_timer = STEP_FRAMES;
        music_step();
        step = (step + 1) % SONG_STEPS;
    }

    if (sfx_seq && --sfx_timer <= 0) {
        if (sfx_seq[0] == 0) {
            sfx_seq = 0;
        } else {
            SND1_SWEEP = 0x0008;
            SND1_CNT = DUTY(2) | ENV(12, 2, 0);
            SND1_FREQ = RESTART | square_rate[sfx_seq[0]];
            sfx_timer = sfx_seq[1];
            sfx_seq += 2;
        }
    }
}

static void sfx_start(const uint8_t *seq)
{
    sfx_seq = seq;
    sfx_timer = 0;
}

void sfx_shoot(void)
{
    sfx_seq = 0;
    SND1_SWEEP = (2 << 4) | 0x08 | 2;   // quick downward sweep
    SND1_CNT = DUTY(2) | ENV(9, 1, 0);
    SND1_FREQ = RESTART | square_rate[84];
}

void sfx_swap(void)
{
    static const uint8_t seq[] = { 79, 2, 84, 3, 0 };
    sfx_start(seq);
}

void sfx_match(int combo)
{
    int up = (combo > 1 ? combo - 1 : 0) * 2;
    if (up > 12) up = 12;
    int k = 0;
    sfx_buf[k++] = 76 + up; sfx_buf[k++] = 3;
    sfx_buf[k++] = 79 + up; sfx_buf[k++] = 3;
    sfx_buf[k++] = 84 + up; sfx_buf[k++] = 6;
    sfx_buf[k] = 0;
    sfx_start(sfx_buf);
}

void sfx_clear(void)
{
    static const uint8_t seq[] = { 72, 6, 76, 6, 79, 6, 84, 20, 0 };
    music_stop();
    sfx_start(seq);
}

void sfx_over(void)
{
    static const uint8_t seq[] = { 67, 12, 64, 12, 60, 12, 55, 30, 0 };
    music_stop();
    sfx_start(seq);
}
