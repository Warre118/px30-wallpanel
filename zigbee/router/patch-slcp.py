#!/usr/bin/env python3
"""patch-slcp.py <project.slcp>: turn a copy of the Gecko SDK's ZigbeeMinimal
sample into the TYZS13 router project: no LED, the UART as CLI stream, the
bootloader interface, the ported "bootloader" CLI commands, our app.c."""
import re, sys, pathlib

p = pathlib.Path(sys.argv[1])
s = p.read_text()
s = re.sub(r"^project_name: .*$", "project_name: tyzs13_router", s, count=1, flags=re.M)
s = re.sub(r"^label: .*$", "label: PX30 TYZS13 Zigbee router", s, count=1, flags=re.M)
# no LED on the module; an explicit USART stream instead of the board's recommended one
s, n = re.subn(r"  - id: simple_led\n    instance:\n      - led0\n", "", s)
assert n == 1, "simple_led"
s, n = re.subn(r"  - id: iostream_recommended_stream\n",
               "  - id: iostream_usart\n    instance:\n      - vcom\n"
               "  - id: bootloader_interface\n", s)
assert n == 1, "iostream_recommended_stream"
s, n = re.subn(r"(configuration:\n)",
               r'\1  - name: SL_CLI_PROMPT_STRING\n    value: "\\"tyzs13_router>\\""\n', s)
assert n == 1, "configuration"
s += """
template_contribution:
  - name: cli_group
    value:
      name: bootloader
      help: Bootloader commands.
  - name: cli_command
    value:
      group: bootloader
      name: info
      handler: router_bootloader_cli_info
      help: Prints bootloader info.
  - name: cli_command
    value:
      group: bootloader
      name: reboot
      handler: router_bootloader_cli_reboot
      help: Reboots into the bootloader.
"""
s, n = re.subn(r"(source:\n  - path: main.c\n  - path: app.c\n)",
               r"\1  - path: router_bootloader_cli.c\n", s)
assert n == 1, "source"
p.write_text(s)
print("ok", p)
