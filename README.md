# Zuma GBA

A Zuma-style marble shooter for the Game Boy Advance.

## Setup (macOS)

### 1. Install devkitPro (GBA toolchain)

Download and run the devkitPro installer for macOS from
<https://github.com/devkitPro/pacman/releases>, then install the GBA tools:

```bash
sudo dkp-pacman -S gba-dev
```

Add the toolchain to your shell (append to `~/.zshrc`):

```bash
export DEVKITPRO=/opt/devkitpro
export DEVKITARM=$DEVKITPRO/devkitARM
export PATH=$DEVKITPRO/tools/bin:$DEVKITARM/bin:$PATH
```

Then reload: `source ~/.zshrc`.

### 2. Install an emulator

[mGBA](https://mgba.io) is recommended:

```bash
brew install --cask mgba
```

### 3. Clone the repo

```bash
git clone https://github.com/hjcoggan/zuma-gba.git ~/zuma-gba
cd ~/zuma-gba
```

Authenticate with the GitHub CLI (`brew install gh && gh auth login`) rather than
putting a token in the clone URL.

### 4. Build and run

```bash
make
open -a mGBA zuma-gba.gba
```

## Controls

| Button | Action |
| --- | --- |
| Left / Right | Aim the frog |
| A | Shoot |
| B | Swap current and next ball |
| Start | Start / restart |

## Project layout

```
source/main.c     Game loop, input, rendering (sprites + tile backgrounds)
source/chain.c    Ball chain logic: spawning, pushing, matching, roll-back combos
source/sound.c    Music and sound effects on the GBA's PSG channels
source/assets.c   Generated: track path, sine table, palettes, tiles, font
include/          Headers (gba.h has the hardware registers)
tools/gen_assets.py  Generates assets.c/assets.h and build/preview.png
tests/            Host-side tests for the chain logic
```

The song is written as text in `source/sound.c` (`lead_src`, `bass_src`,
`drum_src`), so it is easy to edit.

No libraries are needed beyond devkitARM. After editing the artwork or track in
`tools/gen_assets.py`, run `make assets`. Run the logic tests with `make test`.
