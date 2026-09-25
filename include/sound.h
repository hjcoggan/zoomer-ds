// Music and sound effects on the GBA's PSG (Game Boy compatible) channels.
//   ch1 square  - sound effects
//   ch2 square  - lead melody
//   ch3 wave    - bass
//   ch4 noise   - drums
#ifndef SOUND_H
#define SOUND_H

void sound_init(void);
void sound_update(void);     // call once per frame

void music_play(void);       // restart the song from the top
void music_stop(void);       // silence, keeping the song position
void music_resume(void);

void sfx_shoot(void);
void sfx_swap(void);
void sfx_match(int combo);
void sfx_clear(void);
void sfx_over(void);

#endif
