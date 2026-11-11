// TEST HELPER: request GPIO lines as outputs and hold them until killed.
// usage: gpiohold /dev/gpiochipN line=val [line=val ...]
// With "line=pulse" the line is driven low for 200 ms, then high.
#include <fcntl.h>
#include <linux/gpio.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct gpio_v2_line_request req;
	struct gpio_v2_line_values v;
	unsigned long long init = 0, final = 0, mask = 0;
	int fd, i, n = 0;

	if (argc < 3)
		return 2;
	fd = open(argv[1], O_RDWR);
	if (fd < 0) { perror("open"); return 1; }
	memset(&req, 0, sizeof(req));
	for (i = 2; i < argc && n < 8; i++, n++) {
		char *eq = strchr(argv[i], '=');
		if (!eq)
			return 2;
		req.offsets[n] = atoi(argv[i]);
		if (!strcmp(eq + 1, "pulse"))
			final |= 1ULL << n;
		else if (atoi(eq + 1)) {
			init |= 1ULL << n;
			final |= 1ULL << n;
		}
		mask |= 1ULL << n;
	}
	req.num_lines = n;
	strcpy(req.consumer, "gpiohold");
	req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT;
	req.config.num_attrs = 1;
	req.config.attrs[0].attr.id = GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES;
	req.config.attrs[0].attr.values = init;
	req.config.attrs[0].mask = mask;
	if (ioctl(fd, GPIO_V2_GET_LINE_IOCTL, &req) < 0) { perror("get_line"); return 1; }
	usleep(200000);
	v.bits = final;
	v.mask = mask;
	if (ioctl(req.fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &v) < 0) { perror("set"); return 1; }
	printf("holding\n");
	fflush(stdout);
	pause();
	return 0;
}
