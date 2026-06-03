export SHELL = sh
PROGRAM = cpuid
LDFLAGS = -shared
CC = i686-w64-mingw32-gcc
STRIP = i686-w64-mingw32-strip

all: cpuid.dll

cpuid.dll: cpuid.c
	$(CC) -o $@ $(LDFLAGS) $^
	$(STRIP) $@
