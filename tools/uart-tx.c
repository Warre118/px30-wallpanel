/*
 * uart-tx: send N test lines on a UART at a chosen speed (without flow
 * control). To test the chain SoC UART -> pad -> USB adapter.
 * Usage: uart-tx /dev/ttyS5 1500000 20
 * Bouwen:  aarch64-linux-gnu-gcc -static -O2 -o uart-tx uart-tx.c
 */
#include <asm/termbits.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc < 4) { fprintf(stderr, "usage: %s <tty> <baud> <count>\n", argv[0]); return 2; }
	int fd = open(argv[1], O_RDWR | O_NOCTTY);
	if (fd < 0) { perror("open"); return 1; }
	struct termios2 t;
	if (ioctl(fd, TCGETS2, &t)) { perror("TCGETS2"); return 1; }
	t.c_cflag &= ~(CBAUD | CSIZE | PARENB | CSTOPB | CRTSCTS);
	t.c_cflag |= BOTHER | CS8 | CLOCAL | CREAD;
	t.c_iflag = 0; t.c_oflag = 0; t.c_lflag = 0;
	t.c_ispeed = t.c_ospeed = atoi(argv[2]);
	if (ioctl(fd, TCSETS2, &t)) { perror("TCSETS2"); return 1; }
	ioctl(fd, TCGETS2, &t);
	printf("%s: set to %u baud\n", argv[1], t.c_ospeed);
	int n = atoi(argv[3]);
	for (int i = 0; i < n; i++) {
		char buf[80];
		int len = snprintf(buf, sizeof buf, "PX30-UART-TEST %d @ %s baud\r\n", i, argv[2]);
		if (write(fd, buf, len) != len) { perror("write"); return 1; }
		usleep(100000);
	}
	ioctl(fd, TCSBRK, 1);	/* = tcdrain: wait until everything is sent */
	return 0;
}
