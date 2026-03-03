# Auto-detect architecture
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_M),arm64)
    PLATFORM := --platform linux/amd64
else
    PLATFORM :=
endif

# Detect if we're inside Docker
IN_DOCKER := $(shell [ -f /.dockerenv ] && echo "yes" || echo "no")

CC = gcc
CFLAGS = -g

# Variables
SRC = leaky.c
TARGET = leaky

all: $(TARGET)

$(TARGET): $(SRC)
ifeq ($(IN_DOCKER),yes)
	@$(CC) $(CFLAGS) $(SRC) -o $(TARGET)
else
	@docker run $(PLATFORM) --rm -v $(PWD):/work -w /work leax $(CC) $(CFLAGS) $(SRC) -o $(TARGET)
endif

clean:
	@rm -f $(TARGET)

re: clean all

.PHONY: all clean re