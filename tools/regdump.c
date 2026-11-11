/*
 * regdump: dump 32-bit MMIO registers through /dev/mem (read only), to compare
 * the display state under Android and Debian.
 * Usage: regdump <base-hex> <length-hex> [step-hex]
 * Bouwen: aarch64-linux-gnu-gcc -static -O2 -o regdump regdump.c
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc < 3) { fprintf(stderr, "usage: %s <base> <length> [step]\n", argv[0]); return 2; }
	unsigned long base = strtoul(argv[1], 0, 16), len = strtoul(argv[2], 0, 16);
	unsigned long step = argc > 3 ? strtoul(argv[3], 0, 16) : 4;
	int fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) { perror("/dev/mem"); return 1; }
	unsigned long pg = base & ~0xfffUL, off = base - pg, maplen = (off + len + 0xfff) & ~0xfffUL;
	volatile uint32_t *m = mmap(0, maplen, PROT_READ, MAP_SHARED, fd, pg);
	if (m == MAP_FAILED) { perror("mmap"); return 1; }
	for (unsigned long o = 0; o < len; o += step)
		printf("%08lx %08x\n", base + o, m[(off + o) / 4]);
	return 0;
}
