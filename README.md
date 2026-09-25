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

## Project layout

```
source/     C source files
include/    Headers
graphics/   Sprites and backgrounds (converted by grit)
Makefile    devkitPro GBA build
```
