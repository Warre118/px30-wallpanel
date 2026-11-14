#!/usr/bin/env python3
"""
Convert the Wi-Fi network Android currently uses into an iwd profile.
Usage: android-wifi-to-iwd.py <WifiConfigStore.xml> <dumpsys-wifi.txt> <outdir>
NEVER prints the password or the SSID; only writes <outdir>/<name>.psk (0600).
"""
import html, os, re, sys
import xml.etree.ElementTree as ET

store, dump, out = sys.argv[1:4]
cur = re.search(r'mWifiInfo SSID: "?(.*?)"?, BSSID', open(dump, errors="replace").read())
assert cur, "no active Wi-Fi connection found in dumpsys"
current = cur.group(1)

nets = {}
for net in ET.parse(store).getroot().iter("Network"):
    vals = {s.get("name"): s.text for s in net.iter("string")}
    ssid, psk = vals.get("SSID"), vals.get("PreSharedKey")
    if ssid and psk:
        nets[ssid.strip('"')] = psk
assert current in nets, "the active network has no stored PSK"
psk = nets[current]

if psk.startswith('"'):
    body = "[Security]\nPassphrase=%s\n" % psk[1:-1]
else:
    assert re.fullmatch(r"[0-9a-fA-F]{64}", psk), "onbekend PSK-formaat"
    body = "[Security]\nPreSharedKey=%s\n" % psk.lower()
# hostname in the DHCP request: the router shows the panel by name (iwd default: off)
body += "\n[IPv4]\nSendHostname=true\n"

# iwd file name: readable if only [A-Za-z0-9 _-], else =<hex>
name = current if re.fullmatch(r"[A-Za-z0-9 _-]+", current) else "=" + current.encode().hex()
os.makedirs(out, mode=0o700, exist_ok=True)
path = os.path.join(out, name + ".psk")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, body.encode())
os.close(fd)
print("iwd-profiel aangemaakt (%s, %d opgeslagen netwerken bekeken)" %
      ("passphrase" if "Passphrase" in body else "raw PSK", len(nets)))
