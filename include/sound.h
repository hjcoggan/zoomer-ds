// Music and sound effects on the DS's 16 stereo sound channels.
#ifndef SOUND_H
#define SOUND_H

void sound_init(void);
void sound_update(void);     // call once per frame

#define NUM_SONGS 5

void music_play(int song);   // start a song (0 to NUM_SONGS-1) from the top
void music_stop(void);       // silence, keeping the song position
void music_resume(void);

// x is where on screen the sound comes from, for panning
void sfx_shoot(int x);
void sfx_swap(void);
void sfx_insert(int x);
void sfx_match(int combo, int x);
void sfx_contact(void);      // a gap closes
void sfx_power(int kind);    // POW_SLOW, POW_REVERSE or POW_BOMB
void sfx_danger(void);       // heartbeat while the chain nears the pit
void sfx_move(void);         // menu cursor
void sfx_select(void);
void sfx_clear(void);
void sfx_over(void);
void sfx_swallow(void);      // a ball drops into the pit

#endif
