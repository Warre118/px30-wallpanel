/*
 * app.c - TYZS13 (EFR32MG13) stand-alone Zigbee router for the PX30 panel.
 *
 * Joins an open network by itself: network steering at start-up when it has
 * no network, and again every RETRY_MS until it is on one (open "permit join"
 * in ZHA / Zigbee2MQTT and it joins within that time). It needs no host
 * software; the UART only carries the CLI (115200, RTS/CTS), which
 * universal-silabs-flasher uses to identify it and reboot it into the
 * bootloader (router_bootloader_cli.c).
 */
#include "app/framework/include/af.h"
#include "network-steering.h"

#define RETRY_MS (30 * 1000)

static sl_zigbee_event_t join_event;

static void join_event_handler(sl_zigbee_event_t *event)
{
  (void)event;
  if (emberAfNetworkState() == EMBER_NO_NETWORK) {
    EmberStatus status = emberAfPluginNetworkSteeringStart();
    sl_zigbee_app_debug_println("Network steering start: 0x%02X", status);
    if (status != EMBER_SUCCESS) {
      sl_zigbee_event_set_delay_ms(&join_event, RETRY_MS);
    }
  }
}

void emberAfMainInitCallback(void)
{
  sl_zigbee_event_init(&join_event, join_event_handler);
  sl_zigbee_event_set_delay_ms(&join_event, 2000);
}

void emberAfStackStatusCallback(EmberStatus status)
{
  if (status == EMBER_NETWORK_DOWN && emberAfNetworkState() == EMBER_NO_NETWORK) {
    // left the network (or was removed): look for one again
    sl_zigbee_event_set_delay_ms(&join_event, RETRY_MS);
  }
}

void emberAfPluginNetworkSteeringCompleteCallback(EmberStatus status,
                                                  uint8_t totalBeacons,
                                                  uint8_t joinAttempts,
                                                  uint8_t finalState)
{
  (void)totalBeacons;
  (void)finalState;
  sl_zigbee_app_debug_println("Join complete: 0x%02X (%d attempts)", status, joinAttempts);
  if (status != EMBER_SUCCESS) {
    sl_zigbee_event_set_delay_ms(&join_event, RETRY_MS);
  }
}

void emberAfRadioNeedsCalibratingCallback(void)
{
  sl_mac_calibrate_current_channel();
}
