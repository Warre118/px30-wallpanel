/*
 * router_bootloader_cli.c - "bootloader info" and "bootloader reboot" CLI
 * commands for the TYZS13 Zigbee router (Gecko SDK 4.x, EmberZNet 7.x).
 *
 * Port of Nabu Casa's router_bootloader_cli extension
 * (NabuCasa/silabs-firmware-builder, src/zigbee_router/extension/
 * router_bootloader_cli_extension, Apache-2.0) from the Simplicity SDK API
 * (sl_zigbee_af_app_print) to the Gecko SDK 4.x one (emberAfAppPrint).
 * universal-silabs-flasher uses "version" to recognise a router and
 * "bootloader reboot" to get it into the Gecko bootloader, so the router can
 * be reflashed over the UART like the NCP and the RCP (no SWD needed).
 */
#include <stdio.h>

#include "app/framework/include/af.h"
#include "btl_interface.h"
#include "sl_cli.h"

void router_bootloader_cli_info(sl_cli_command_arg_t *arguments)
{
  (void)arguments;
  BootloaderInformation_t info = { .type = SL_BOOTLOADER, .version = 0L, .capabilities = 0L };
  bootloader_getInfo(&info);
  emberAfAppPrint("%s Bootloader v%u.%02u.%02u\n",
                  info.type == SL_BOOTLOADER ? "Gecko" : "Legacy",
                  (unsigned)((info.version >> 24) & 0xFF),
                  (unsigned)((info.version >> 16) & 0xFF),
                  (unsigned)(info.version & 0xFFFF));
}

void router_bootloader_cli_reboot(sl_cli_command_arg_t *arguments)
{
  (void)arguments;
  bootloader_rebootAndInstall();
}
