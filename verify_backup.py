"""Compares the PC backup with the sha256 hashes computed on the panel.
Every partition is checked as a byte range inside emmc-full.img, and so are
the separate partition images and boot0/boot1."""
import hashlib, os, sys

SECTOR = 512
run = sys.argv[1]

def sha(path, offset=0, length=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        left = length if length is not None else float("inf")
        while left > 0:
            chunk = f.read(int(min(8 << 20, left)))
            if not chunk:
                break
            h.update(chunk); left -= len(chunk)
    return h.hexdigest()

panel = {}
for line in open(os.path.join(run, "sha256-panel.txt")):
    if line.strip():
        digest, name = line.split()
        panel[name] = digest

parts = {}
for line in open(os.path.join(run, "partitions.txt")):
    dev, start, size, name = line.split()
    parts[dev] = (int(start), int(size), name)

full = os.path.join(run, "emmc-full.img")
expected_full = int(open(os.path.join(run, "emmc-info.txt")).readline()) * SECTOR
results = []
def check(label, got, dev):
    want = panel.get(dev)
    ok = "OK  " if got == want else "DIFF"
    results.append(ok == "OK  ")
    print(f"{ok} {label:34s} {got[:16]}  panel {str(want)[:16]}")

size = os.path.getsize(full)
print(f"emmc-full.img: {size} bytes, expected {expected_full} -> {'OK' if size == expected_full else 'WRONG'}")
if "mmcblk1" in panel:
    check("emmc-full.img (geheel)", sha(full), "mmcblk1")
man = os.path.join(run, "chunks.sha256")
if os.path.exists(man):
    live = [l.split()[0] for l in open(man) if l.strip().endswith("LIVE")]
    print(f"blocks with live data (changed during the dump): {live or 'none'}")
for b in ("mmcblk1boot0", "mmcblk1boot1"):
    check(f"{b}.img", sha(os.path.join(run, b + ".img")), b)
for dev, (start, size_s, name) in sorted(parts.items(), key=lambda kv: kv[1][0]):
    check(f"{dev} {name} (in full.img)", sha(full, start * SECTOR, size_s * SECTOR), dev)
    single = os.path.join(run, "partitions", name + ".img")
    if os.path.exists(single):
        check(f"partitions/{name}.img", sha(single), dev)
print(f"\n{sum(results)}/{len(results)} equal. Differences in userdata/cache/metadata"
      " and the whole image are expected while Android runs (they change during the dump).")
