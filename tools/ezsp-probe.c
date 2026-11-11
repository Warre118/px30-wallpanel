/*
 * ezsp-probe - reads identification from a Silicon Labs EZSP NCP (Zigbee module).
 *
 * Only sends READ commands:
 *   ASH RST (restarts the Zigbee chip, as on every coordinator start)
 *   EZSP version, getValue(VERSION_INFO), getEui64, getMfgToken (various),
 *   getStandaloneBootloaderVersionPlatMicroPhy, getXncpInfo, networkState
 * Changes no firmware, no tokens, no network; writes nothing to storage.
 * Does NOT start the bootloader.
 *
 * Build:   aarch64-linux-gnu-gcc -static -O2 -Wall -o ezsp-probe ezsp-probe.c
 * Usage:   ezsp-probe [/dev/ttyS5] [115200] [rtscts|none]
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>

static int fd;
static uint8_t ash_tx;      /* next frmNum we send */
static uint8_t ash_ack;     /* next frmNum we expect from the NCP */
static uint8_t ezsp_seq;
static int ezsp_ver = 4;    /* >= 8: extended frame format */

/* ------------------------------------------------------------- ASH layer */

static uint16_t crc_ccitt(const uint8_t *p, size_t n)
{
	uint16_t crc = 0xFFFF;
	while (n--) {
		crc ^= (uint16_t)(*p++) << 8;
		for (int i = 0; i < 8; i++)
			crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
	}
	return crc;
}

static void ash_randomize(uint8_t *p, size_t n)
{
	uint8_t r = 0x42;
	for (size_t i = 0; i < n; i++) {
		p[i] ^= r;
		r = (r & 1) ? (r >> 1) ^ 0xB8 : r >> 1;
	}
}

static int reserved(uint8_t b)
{
	return b == 0x7E || b == 0x7D || b == 0x11 || b == 0x13 || b == 0x18 || b == 0x1A;
}

static void ash_send_raw(const uint8_t *frame, size_t n)
{
	uint8_t buf[300], out[620];
	size_t o = 0;
	memcpy(buf, frame, n);
	uint16_t crc = crc_ccitt(buf, n);
	buf[n++] = crc >> 8;
	buf[n++] = crc & 0xFF;
	for (size_t i = 0; i < n; i++) {
		if (reserved(buf[i])) { out[o++] = 0x7D; out[o++] = buf[i] ^ 0x20; }
		else out[o++] = buf[i];
	}
	out[o++] = 0x7E;
	if (write(fd, out, o) != (ssize_t)o)
		perror("write");
}

static int ash_read_frame(uint8_t *out, size_t max, int timeout_ms)
{
	size_t n = 0;
	int esc = 0;
	for (;;) {
		fd_set rf;
		struct timeval tv = { timeout_ms / 1000, (timeout_ms % 1000) * 1000 };
		FD_ZERO(&rf);
		FD_SET(fd, &rf);
		if (select(fd + 1, &rf, NULL, NULL, &tv) <= 0)
			return -1;
		uint8_t b;
		if (read(fd, &b, 1) != 1)
			return -1;
		if (b == 0x1A) { n = 0; esc = 0; continue; }
		if (b == 0x11 || b == 0x13) continue;
		if (b == 0x7E) { if (n == 0) continue; return (int)n; }
		if (b == 0x7D) { esc = 1; continue; }
		if (esc) { b ^= 0x20; esc = 0; }
		if (n < max) out[n++] = b;
	}
}

static void ash_send_ack(void)
{
	uint8_t c = 0x80 | (ash_ack & 7);
	ash_send_raw(&c, 1);
}

static void ash_send_data(const uint8_t *payload, size_t n)
{
	uint8_t frame[260];
	frame[0] = ((ash_tx & 7) << 4) | (ash_ack & 7);
	memcpy(frame + 1, payload, n);
	ash_randomize(frame + 1, n);
	ash_send_raw(frame, n + 1);
	ash_tx = (ash_tx + 1) & 7;
}

/* waits for a DATA frame; ACKs it; returns the de-randomised payload */
static int ash_recv_data(uint8_t *out, size_t max)
{
	uint8_t f[300];
	for (int tries = 0; tries < 10; tries++) {
		int n = ash_read_frame(f, sizeof f, 3000);
		if (n < 0) return -1;
		if (n < 3) continue;
		if (crc_ccitt(f, n - 2) != ((f[n - 2] << 8) | f[n - 1])) continue;
		if (f[0] & 0x80) continue;                   /* ACK/NAK/RSTACK/ERROR */
		ash_ack = ((f[0] >> 4) + 1) & 7;
		ash_send_ack();
		int dn = n - 3;
		if ((size_t)dn > max) dn = max;
		memcpy(out, f + 1, dn);
		ash_randomize(out, dn);
		return dn;
	}
	return -1;
}

static int ash_reset(void)
{
	const uint8_t rst[] = { 0x1A, 0xC0, 0x38, 0xBC, 0x7E };
	if (write(fd, rst, sizeof rst) != sizeof rst) return -1;
	uint8_t f[64];
	for (int tries = 0; tries < 5; tries++) {
		int n = ash_read_frame(f, sizeof f, 3000);
		if (n < 0) return -1;
		if (f[0] == 0xC1 && n >= 3) {
			printf("ASH        : RSTACK, ASH version %u, reset reason 0x%02X\n", f[1], f[2]);
			ash_tx = ash_ack = 0;
			return 0;
		}
	}
	return -1;
}

/* ------------------------------------------------------------ EZSP layer */

/* sends a command; copies the parameters of the answer to resp */
static int ezsp_cmd(uint16_t id, const uint8_t *par, size_t pn, uint8_t *resp, size_t rmax)
{
	uint8_t p[260], r[300];
	size_t n = 0;
	p[n++] = ezsp_seq;
	if (ezsp_ver >= 8) {
		p[n++] = 0x00;          /* frame control low: command */
		p[n++] = 0x01;          /* frame control high: frame format v1 */
		p[n++] = id & 0xFF;
		p[n++] = id >> 8;
	} else if (ezsp_ver >= 5) {
		p[n++] = 0x00;          /* frame control */
		p[n++] = 0xFF;          /* EZSP v5-v7: extended header marker */
		p[n++] = 0x00;
		p[n++] = id & 0xFF;
	} else {
		p[n++] = 0x00;
		p[n++] = id & 0xFF;
	}
	memcpy(p + n, par, pn);
	n += pn;
	ash_send_data(p, n);
	for (int tries = 0; tries < 5; tries++) {
		int rn = ash_recv_data(r, sizeof r);
		if (rn < 0) return -1;
		int hdr = ezsp_ver >= 5 ? 5 : 3;
		if (rn < hdr || r[0] != ezsp_seq) continue;    /* callback or other frame */
		uint16_t rid = ezsp_ver >= 8 ? (r[3] | (r[4] << 8)) :
			       ezsp_ver >= 5 ? r[4] : r[2];
		if (rid != id) continue;
		ezsp_seq++;
		int pl = rn - hdr;
		if ((size_t)pl > rmax) pl = rmax;
		memcpy(resp, r + hdr, pl);
		return pl;
	}
	return -1;
}

static void print_bytes(const char *label, const uint8_t *p, int n)
{
	printf("%-11s:", label);
	for (int i = 0; i < n; i++) printf(" %02X", p[i]);
	printf("   \"");
	for (int i = 0; i < n; i++) putchar(p[i] >= 0x20 && p[i] < 0x7F ? p[i] : '.');
	printf("\"\n");
}

static void mfg_token(uint8_t tok, const char *label)
{
	uint8_t r[64];
	int n = ezsp_cmd(0x000B, &tok, 1, r, sizeof r);       /* getMfgToken */
	if (n < 1) { printf("%-11s: no answer\n", label); return; }
	int len = r[0] < n - 1 ? r[0] : n - 1;
	int blank = 1;
	for (int i = 0; i < len; i++) if (r[1 + i] != 0xFF) blank = 0;
	if (len == 0 || blank) { printf("%-11s: (empty)\n", label); return; }
	print_bytes(label, r + 1, len);
}

int main(int argc, char **argv)
{
	const char *dev = argc > 1 ? argv[1] : "/dev/ttyS5";
	int baud = argc > 2 ? atoi(argv[2]) : 115200;
	int rtscts = !(argc > 3 && strcmp(argv[3], "none") == 0);
	speed_t sp = baud == 57600 ? B57600 : baud == 230400 ? B230400 :
		     baud == 460800 ? B460800 : B115200;

	fd = open(dev, O_RDWR | O_NOCTTY);
	if (fd < 0) { perror(dev); return 2; }
	struct termios t;
	tcgetattr(fd, &t);
	cfmakeraw(&t);
	cfsetispeed(&t, sp);
	cfsetospeed(&t, sp);
	t.c_cflag |= CLOCAL | CREAD;
	t.c_cflag &= ~CSTOPB;
	if (rtscts) t.c_cflag |= CRTSCTS; else t.c_cflag &= ~CRTSCTS;
	t.c_cc[VMIN] = 0;
	t.c_cc[VTIME] = 0;
	tcsetattr(fd, TCSANOW, &t);
	tcflush(fd, TCIOFLUSH);
	printf("port       : %s @ %d, flow control %s\n", dev, baud, rtscts ? "RTS/CTS" : "none");

	if (ash_reset() < 0) {
		printf("RESULT     : no RSTACK (module in reset, other port/baud/flow control?)\n");
		return 1;
	}

	/* version: first legacy with protocol 4, then with the NCP version */
	uint8_t r[64], v = 4;
	int n = ezsp_cmd(0x0000, &v, 1, r, sizeof r);
	if (n < 4) { printf("RESULT     : RSTACK but no version answer\n"); return 1; }
	printf("version(4) : NCP reports protocol %u, stack type %u, stack 0x%04X\n",
	       r[0], r[1], r[2] | (r[3] << 8));
	if (r[0] != 4) {
		ezsp_ver = r[0];
		v = r[0];
		n = ezsp_cmd(0x0000, &v, 1, r, sizeof r);
		if (n < 4) { printf("RESULT     : no answer to version %u\n", v); return 1; }
	}
	unsigned sv = r[2] | (r[3] << 8);
	printf("EZSP       : protocol %u, stack type %u, stack version %u.%u.%u.%u (0x%04X)\n",
	       r[0], r[1], sv >> 12, (sv >> 8) & 0xF, (sv >> 4) & 0xF, sv & 0xF, sv);

	uint8_t id = 0x11;                                     /* EZSP_VALUE_VERSION_INFO */
	n = ezsp_cmd(0x00AA, &id, 1, r, sizeof r);
	if (n >= 9 && r[0] == 0)
		printf("EmberZNet  : %u.%u.%u.%u build %u (type %u)\n",
		       r[4], r[5], r[6], r[7], r[2] | (r[3] << 8), r[8]);

	n = ezsp_cmd(0x0091, NULL, 0, r, sizeof r);           /* bootloader/plat/micro/phy */
	if (n >= 5) {
		unsigned bl = r[0] | (r[1] << 8);
		printf("bootloader : %s0x%04X, plat %u, micro %u, phy %u\n",
		       bl == 0xFFFF ? "NO standalone bootloader reported, " : "version ",
		       bl, r[2], r[3], r[4]);
	}

	n = ezsp_cmd(0x0026, NULL, 0, r, sizeof r);           /* getEui64 */
	if (n >= 8) {
		printf("EUI-64     : ");
		for (int i = 7; i >= 0; i--) printf("%02X%s", r[i], i ? ":" : "\n");
	}

	n = ezsp_cmd(0x0013, NULL, 0, r, sizeof r);           /* getXncpInfo */
	if (n >= 5 && r[0] == 0)
		printf("XNCP       : manufacturer 0x%04X, version 0x%04X\n",
		       r[1] | (r[2] << 8), r[3] | (r[4] << 8));
	else
		printf("XNCP       : not supported\n");

	mfg_token(0x00, "custom-ver");
	mfg_token(0x01, "mfg-string");
	mfg_token(0x02, "board-name");
	mfg_token(0x03, "manuf-id");
	mfg_token(0x04, "phy-config");
	mfg_token(0x06, "ash-config");
	mfg_token(0x0C, "custom-eui");
	mfg_token(0x0D, "ctune");

	n = ezsp_cmd(0x0018, NULL, 0, r, sizeof r);           /* networkState */
	if (n >= 1)
		printf("network    : status %u (0 = no network, 2 = joined)\n", r[0]);

	printf("RESULT     : OK\n");
	return 0;
}
