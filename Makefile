#---------------------------------------------------------------------------------
# Zoomer DS - built with devkitARM + libnds (devkitPro's nds-dev package)
#---------------------------------------------------------------------------------
.SUFFIXES:

ifeq ($(strip $(DEVKITARM)),)
$(error "Please set DEVKITARM in your environment. export DEVKITARM=<path to>devkitARM")
endif

GAME_TITLE     := Zoomer DS
GAME_SUBTITLE1 := An Aztec marble shooter
GAME_SUBTITLE2 := Made with Claude

include $(DEVKITARM)/ds_rules

TARGET   := zoomer-ds
BUILD    := build
SOURCES  := source
INCLUDES := include
DATA     := data
ICON     := icon.bmp

ARCH := -march=armv5te -mtune=arm946e-s

CFLAGS   := -g -Wall -Wextra -O2 -ffunction-sections -fdata-sections -std=gnu11 \
            $(ARCH) $(INCLUDE) -DARM9
ASFLAGS  := -g $(ARCH)
# make AUTOPLAY=<level> (0 = endless) builds a debug ROM that plays itself
ifneq ($(strip $(AUTOPLAY)),)
CFLAGS += -DAUTOPLAY=$(AUTOPLAY)
endif
# make AUTOTEST=1 builds a debug ROM that steps through every menu and mode
ifneq ($(strip $(AUTOTEST)),)
CFLAGS += -DAUTOTEST
endif
# make DEBUGHUD=1 shows a frame counter and a crash dump screen
ifneq ($(strip $(DEBUGHUD)),)
CFLAGS += -DDEBUGHUD
endif
LDFLAGS   = -specs=ds_arm9.specs -g $(ARCH) -Wl,-Map,$(notdir $*.map)

LIBS    := -lfat -lnds9 -lm
LIBDIRS := $(LIBNDS) $(PORTLIBS)

ifneq ($(BUILD),$(notdir $(CURDIR)))
#---------------------------------------------------------------------------------

export OUTPUT := $(CURDIR)/$(TARGET)
export VPATH  := $(foreach dir,$(SOURCES),$(CURDIR)/$(dir)) $(foreach dir,$(DATA),$(CURDIR)/$(dir))
export DEPSDIR := $(CURDIR)/$(BUILD)

CFILES   := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
BINFILES := $(foreach dir,$(DATA),$(notdir $(wildcard $(dir)/*.bin)))

export LD := $(CC)
export OFILES_BIN     := $(addsuffix .o,$(BINFILES))
export OFILES_SOURCES := $(CFILES:.c=.o)
export OFILES := $(OFILES_BIN) $(OFILES_SOURCES)
export HFILES := $(addsuffix .h,$(subst .,_,$(BINFILES)))

export INCLUDE  := $(foreach dir,$(INCLUDES),-iquote $(CURDIR)/$(dir)) \
                   $(foreach dir,$(LIBDIRS),-I$(dir)/include) -I$(CURDIR)/$(BUILD)
export LIBPATHS := $(foreach dir,$(LIBDIRS),-L$(dir)/lib)
export GAME_ICON := $(CURDIR)/$(BUILD)/$(notdir $(basename $(ICON))).grf

.PHONY: $(BUILD) clean assets test

$(BUILD):
	@mkdir -p $@
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile

# Regenerate the artwork (data/*.bin, source/assets.c, source/tracks.c,
# include/assets.h, icon.bmp)
assets:
	python3 tools/gen_assets.py

# Host-side tests for the ball-chain logic
test:
	@mkdir -p $(BUILD)
	cc -std=c99 -Wall -Wextra -Iinclude tests/test_chain.c source/chain.c source/tracks.c -o $(BUILD)/test_chain
	./$(BUILD)/test_chain

clean:
	@echo clean ...
	@rm -fr $(BUILD) $(TARGET).elf $(TARGET).nds

#---------------------------------------------------------------------------------
else

$(OUTPUT).nds: $(OUTPUT).elf $(GAME_ICON)
$(OUTPUT).elf: $(OFILES)

$(OFILES_SOURCES) : $(HFILES)

%.bin.o %_bin.h : %.bin
	@echo $(notdir $<)
	@$(bin2o)

$(GAME_ICON): $(CURDIR)/../$(ICON)
	@echo convert $(notdir $<)
	@grit $< -g -gt -gB4 -gT FF00FF -m! -p -pe 16 -fh! -ftr -o$@

-include $(DEPSDIR)/*.d

endif
