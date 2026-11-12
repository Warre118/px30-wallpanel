"""fwpipe - radio firmware update pipeline (TubesZB MGM210 + PX30 wall panel TYZS13).

Watches the upstream sources, builds the firmware per device / firmware type /
channel in throw-away builder containers, and publishes the result (with the
previous build archived) to a firmware repository the devices read.
See updater/README.md.
"""
__version__ = "0.1.0"
# Bump when the way firmware is built changes in a way that must trigger rebuilds.
RECIPE_VERSION = 1
