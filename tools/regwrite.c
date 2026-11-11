/* regwrite <addr-hex> <value-hex> : one 32-bit write through /dev/mem, with read back (debug). */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc < 3) { fprintf(stderr, "usage: %s <addr> <value>\n", argv[0]); return 2; }
	unsigned long a = strtoul(argv[1], 0, 16), page = a & ~0xfffUL;
	uint32_t v = strtoul(argv[2], 0, 16);
	int fd = open("/dev/mem", O_RDWR | O_SYNC);
	if (fd < 0) { perror("/dev/mem"); return 1; }
	volatile uint32_t *m = mmap(0, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, page);
	if (m == MAP_FAILED) { perror("mmap"); return 1; }
	volatile uint32_t *r = m + (a - page) / 4;
	uint32_t old = *r;
	*r = v;
	printf("%08lx: %08x -> %08x\n", a, old, *r);
	return 0;
}
