// The playfield on the bottom screen, drawn by the DS 3D engine (libnds gl2d):
// rolling balls, the rotating frog, the aim guide and particle effects.
// Everything is drawn in screen pixels on top of the level picture.
#ifndef SCENE_H
#define SCENE_H

#include <stdint.h>

void scene_init(void);
void scene_begin(int shake_x, int shake_y);     // start a frame; everything is offset by the shake
void scene_end(void);                           // hand the frame to the 3D engine

// A ball centred on (x, y). frame is the rolling frame, angle the direction it
// rolls in (32768 a turn). scale is 4096 for full size.
void draw_ball(int x, int y, int color, int frame, int angle, int power, int scale);
void draw_frog(int x, int y, int angle, int blink);
void draw_dot(int x, int y, uint16_t rgb, int alpha);

// effects, all in screen pixels
void fx_clear(void);
void fx_burst(int x, int y, uint16_t rgb, int n);        // sparks flying out
void fx_ring(int x, int y, uint16_t rgb, int big);       // expanding shockwave
void fx_stars(int x, int y, int n);                      // gold stars
void fx_popup(int x, int y, int points, int combo);      // floating "+120 X2"
void fx_update(void);
void fx_draw(void);

#endif
