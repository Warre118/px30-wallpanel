// SPDX-License-Identifier: GPL-2.0
/*
 * TEST HELPER: marks /serial@ff158000/bluetooth as status = "disabled" in the
 * live device tree, so that after an unbind/bind of ff158000.serial the port
 * comes back as a plain tty (/dev/ttyS1) instead of a serdev controller.
 * rmmod (or a reboot) restores the node.
 */
#include <linux/module.h>
#include <linux/of.h>

static struct of_changeset ocs;
static struct device_node *np;

static int __init btuart_free_init(void)
{
	int ret;

	np = of_find_node_by_path("/serial@ff158000/bluetooth");
	if (!np)
		return -ENODEV;

	of_changeset_init(&ocs);
	ret = of_changeset_add_prop_string(&ocs, np, "status", "disabled");
	if (!ret)
		ret = of_changeset_apply(&ocs);
	if (ret) {
		of_changeset_destroy(&ocs);
		of_node_put(np);
		return ret;
	}
	pr_info("btuart_free: bluetooth node disabled\n");
	return 0;
}

static void __exit btuart_free_exit(void)
{
	of_changeset_revert(&ocs);
	of_changeset_destroy(&ocs);
	of_node_put(np);
}

module_init(btuart_free_init);
module_exit(btuart_free_exit);
MODULE_DESCRIPTION("PX30 wallpanel: free uart1 from serdev for BT tests");
MODULE_LICENSE("GPL");
