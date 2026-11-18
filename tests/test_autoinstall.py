"""Offline tests for px30-rescue's cableless autoinstall (do_autoinstall).

No hardware, no root: the stock-Android payload is packed exactly as
tools/px30-android-autoinstall.sh packs it (a ustar tar of px30-autoinstall/
with PAYLOAD.sha256), laid at offset 0 of a small image that stands in for the
stback partition, and the real do_autoinstall() shell function is sourced out
of initramfs/bin/px30-rescue and run under /bin/sh with every hardware- and
state-touching helper (ai_present, the writes, repartition, state) stubbed.

The guard of interest: the 30 s countdown must not re-read VERSION from stback.
Each ai_get streams the whole stback region through tar, so a per-iteration read
meant ~30 extra full reads. do_autoinstall reads VERSION exactly three times
(verify pass, the one cached read for the screens, factory copy) no matter how
many times the countdown loops -- the test checks that the countdown really
iterates (screen is called many times) while VERSION is still read only 3x.

    python3 tests/test_autoinstall.py
    python3 -m unittest discover -s tests
"""
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "initramfs/bin/px30-rescue")
IMG_MIB = 8

# files the real packer puts in px30-autoinstall/ (PAYLOAD.sha256 lists all but itself)
MEMBERS = {
    "px30-boot.img": b"BOOT\n",
    "px30-rescue.img": b"RESCUE\n",
    "px30-rootfs.img.zst": b"\x28\xb5\x2f\xfd" + b"rootfs-payload" * 64,
    "VERSION": b"release-2026.11.14-TEST\n",
    "SHA256SUMS": b"(stand-in release manifest)\n",
}


def read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def function_slice():
    """The function definitions of px30-rescue, up to the top-level dispatch."""
    out = []
    with open(SRC, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if i < 26:
                continue
            if line.startswith('case "$cmd" in'):
                break
            out.append(line)
    return "".join(out)


def build_image(path, corrupt=False):
    """Pack px30-autoinstall/ like the tool and write the tar at offset 0."""
    files = dict(MEMBERS)
    payload = "".join(
        f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in files
    ).encode()
    if corrupt:  # tamper after the checksums are fixed -> sha mismatch on read
        files["px30-rootfs.img.zst"] = b"TAMPERED-DIFFERENT-CONTENT\n"
    members = {**files, "PAYLOAD.sha256": payload}

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(f"px30-autoinstall/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    assert len(blob) < IMG_MIB * 1024 * 1024, "payload larger than the fake stback"
    with open(path, "wb") as fh:
        fh.write(blob)
        fh.truncate(IMG_MIB * 1024 * 1024)


HARNESS = r"""
export TTY=/dev/null
. "$FUNCS"
# stubs: everything do_autoinstall touches except the logic under test
say()  { echo "$*"; }                       # the real say also writes /dev/kmsg
screen() { echo x >> "$SCRLOG"; }           # count how often the countdown redraws
sleep()  { :; }                             # run the 30 s countdown instantly
ai_present() { AI_START=0; AI_COUNT=$IMG_MIB; return 0; }
ai_get() { echo "$1" >> "$LOG"
           dd if="$IMG" bs=1048576 skip=$AI_START count=$AI_COUNT 2>/dev/null \
             | tar -xOf - "$AI_DIR/$1" 2>/dev/null; }
do_repartition()  { return 0; }
do_write_rootfs() { cat >/dev/null; return 0; }
do_write_part()   { cat >/dev/null; return 0; }
do_put_factory()  { cat >/dev/null; return 0; }
state_save() { :; }; bcb_clear() { :; }; state_load() { :; }; need() { echo /dev/null; }
rm -f /tmp/hold
do_autoinstall
echo "rc=$?"
"""


class Autoinstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.funcs = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
        cls.funcs.write(function_slice())
        cls.funcs.close()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.funcs.name)

    def run_autoinstall(self, corrupt=False):
        d = tempfile.mkdtemp()
        img = os.path.join(d, "stback.img")
        log = os.path.join(d, "aiget.log")
        scr = os.path.join(d, "screen.log")
        build_image(img, corrupt=corrupt)
        env = {**os.environ, "FUNCS": self.funcs.name, "IMG": img,
               "LOG": log, "SCRLOG": scr, "IMG_MIB": str(IMG_MIB)}
        p = subprocess.run(["sh", "-c", HARNESS], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ai = read(log).split()
        screens = len(read(scr).splitlines())
        rc = next((int(l[3:]) for l in p.stdout.splitlines() if l.startswith("rc=")), None)
        return p.stdout, ai, screens, rc

    def test_countdown_does_not_reread_version(self):
        out, ai, screens, rc = self.run_autoinstall()
        self.assertIn("Autoinstall done", out)
        self.assertEqual(rc, 0, out)
        # the countdown really looped (30 redraws + a few other screens)...
        self.assertGreaterEqual(screens, 30, out)
        # ...yet VERSION was read exactly 3x: verify pass, the cached read, factory copy
        self.assertEqual(ai.count("VERSION"), 3,
                         f"VERSION read {ai.count('VERSION')}x; countdown must not re-read it")

    def test_happy_path_writes_in_order(self):
        out, ai, screens, rc = self.run_autoinstall()
        self.assertEqual(rc, 0, out)
        # rootfs/boot/recovery each fetched for the slot write and again for factory
        for m in ("px30-rootfs.img.zst", "px30-boot.img", "px30-rescue.img"):
            self.assertGreaterEqual(ai.count(m), 2, out)
        self.assertIn("Autoinstall done (rootfs_b", out)

    def test_corrupted_payload_rejected_before_write(self):
        out, ai, screens, rc = self.run_autoinstall(corrupt=True)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("px30-rootfs.img.zst damaged", out)
        self.assertNotIn("Installing release", out)        # aborted before the write phase


if __name__ == "__main__":
    unittest.main(verbosity=2)
