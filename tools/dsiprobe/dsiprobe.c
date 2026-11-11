// SPDX-License-Identifier: GPL-2.0
/*
 * dsiprobe: diagnostics for the ST7701 panel. Reads DCS registers through the
 * panel's existing mipi_dsi_device and can send single DCS commands.
 *   insmod dsiprobe.ko                -> reads 0x0A/0x0B/0x0C/0x0D/0x04/0xDA-0xDC
 *   insmod dsiprobe.ko cmd=0x23       -> sends DCS 0x23 (all pixels on) and reads again
 *   insmod dsiprobe.ko cmd=0x11,0x29  -> meerdere korte commando's (120 ms ertussen)
 * The module does not stay loaded (init returns -EAGAIN); results in dmesg.
 */
#include <drm/drm_mipi_dsi.h>
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/of.h>

#include "smatek-init.h"

static int cmd[8];
static int ncmd;
module_param_array(cmd, int, &ncmd, 0);
/* vendor=1: display off + sleep in, then replay the Android init literally */
static int vendor;
module_param(vendor, int, 0);

static void play_vendor(struct mipi_dsi_device *dsi)
{
	const u8 *p = smatek_px30_480_init, *end = p + sizeof(smatek_px30_480_init);
	int n = 0, err = 0;

	mipi_dsi_dcs_write_buffer(dsi, (u8[]){ 0x28 }, 1);
	mipi_dsi_dcs_write_buffer(dsi, (u8[]){ 0x10 }, 1);
	msleep(120);
	while (p < end) {
		u8 delay = p[0], len = p[1];

		if (mipi_dsi_dcs_write_buffer(dsi, p + 2, len) < 0)
			err++;
		if (delay)
			msleep(delay);
		p += 2 + len;
		n++;
	}
	pr_info("dsiprobe: vendor-init afgespeeld: %d commando's, %d fouten\n", n, err);
}

static void rd(struct mipi_dsi_device *dsi, u8 reg, size_t len)
{
	u8 buf[4] = {};
	ssize_t ret = mipi_dsi_dcs_read(dsi, reg, buf, len);

	if (ret < 0)
		pr_info("dsiprobe: read 0x%02x -> error %zd\n", reg, ret);
	else
		pr_info("dsiprobe: read 0x%02x -> %zd bytes: %*ph\n", reg, ret, (int)len, buf);
}

static void dump(struct mipi_dsi_device *dsi)
{
	rd(dsi, 0x0A, 1);	/* power mode: b7 booster, b4 sleep out, b2 display on */
	rd(dsi, 0x0B, 1);	/* MADCTL */
	rd(dsi, 0x0C, 1);	/* pixel format */
	rd(dsi, 0x0D, 1);	/* display mode */
	rd(dsi, 0x04, 3);	/* display ID */
	rd(dsi, 0xDA, 1);
	rd(dsi, 0xDB, 1);
	rd(dsi, 0xDC, 1);
}

static int __init dsiprobe_init(void)
{
	struct device_node *np;
	struct mipi_dsi_device *dsi;
	int i;

	np = of_find_compatible_node(NULL, NULL, "smatek,px30-wallpanel-panel");
	if (!np)
		return -ENODEV;
	dsi = of_find_mipi_dsi_device_by_node(np);
	of_node_put(np);
	if (!dsi) {
		pr_info("dsiprobe: no mipi_dsi_device\n");
		return -ENODEV;
	}
	pr_info("dsiprobe: device %s lanes=%u format=%d flags=0x%lx\n",
		dev_name(&dsi->dev), dsi->lanes, dsi->format, dsi->mode_flags);
	dump(dsi);
	for (i = 0; i < ncmd; i++) {
		u8 c = cmd[i];
		ssize_t ret = mipi_dsi_dcs_write_buffer(dsi, &c, 1);

		pr_info("dsiprobe: write 0x%02x -> %zd\n", c, ret);
		msleep(120);
	}
	if (vendor)
		play_vendor(dsi);
	if (ncmd || vendor)
		dump(dsi);
	put_device(&dsi->dev);
	return -EAGAIN;
}
module_init(dsiprobe_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("DCS-diagnose ST7701 Smatek-paneel");
