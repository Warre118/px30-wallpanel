#!/usr/bin/env python3
"""
Builds a boot/recovery image for the vendor U-Boot of the Smatek PX30 panel:
an Android boot image v0 (same layout as the original boot/recovery.img)
with our mainline kernel, and as "second" a Rockchip RSCE resource holding
only rk-kernel.dtb.

Usage: make-recovery-img.py <Image> <dtb> <cmdline.txt> <out.img> [initramfs.cpio.gz]
With initramfs: the PX30 initramfs (initramfs/), for boot (px30.mode=normal)
and recovery (px30.mode=rescue). Without: an empty cpio, root= from the cmdline.
Writes nothing to the panel.
"""
import hashlib, struct, sys

PAGE = 2048
BLK = 512
PART_SIZE = 64 << 20          # recovery partition = 0x20000 sectors
# load addresses as in the original (the vendor U-Boot uses its own addresses)
KERNEL_ADDR, RAMDISK_ADDR, SECOND_ADDR, TAGS_ADDR = 0x10008000, 0x11000000, 0x10F00000, 0x10000100
OS_VERSION = 0x10040137       # taken from the original recovery.img (Android 8.1)


def pad(b, n):
    return b + b"\0" * (-len(b) % n)


def rsce(files):
    """Rockchip resource: 1 block header, 1 block per entry, data block-aligned."""
    hdr = struct.pack("<4sHHBBBxI", b"RSCE", 0, 0, 1, 1, 1, len(files))
    hdr = pad(hdr, BLK)
    entries, data = b"", b""
    off = 1 + len(files)                       # in blocks
    for name, content in files:
        e = b"ENTR" + name.encode().ljust(256, b"\0") + struct.pack("<II", off + len(data) // BLK, len(content))
        entries += pad(e, BLK)
        data += pad(content, BLK)
    return hdr + entries + data


def empty_cpio():
    """Minimal initramfs: only /dev and the TRAILER, NO /init. The kernel unpacks
    it, finds no /init and then simply mounts root= (prepare_namespace).
    Needed because the vendor U-Boot does not start a recovery without a ramdisk."""
    def entry(name, mode, nlink):
        n = name.encode() + b"\0"
        h = b"070701" + b"".join(b"%08X" % v for v in (0, mode, 0, 0, nlink, 0, 0, 0, 0, 0, 0, len(n), 0))
        return pad(h + n, 4)
    return pad(entry("dev", 0o040755, 2) + entry("TRAILER!!!", 0, 1), BLK)


def main(image, dtb, cmdfile, out, initrd=None):
    kernel = open(image, "rb").read()
    fdt = open(dtb, "rb").read()
    cmdline = open(cmdfile).readline().strip().encode()
    assert kernel[0x38:0x3C] == b"ARM\x64", "not an arm64 Image"
    assert fdt[:4] == b"\xd0\x0d\xfe\xed", "not a DTB"
    assert len(cmdline) < 512, "cmdline te lang (max 511)"
    second = rsce([("rk-kernel.dtb", fdt)])
    ramdisk = open(initrd, "rb").read() if initrd else empty_cpio()
    # id = sha1 over (kernel, len, ramdisk, len, second, len) like mkbootimg
    sha = hashlib.sha1()
    for blob in (kernel, ramdisk, second):
        sha.update(blob)
        sha.update(struct.pack("<I", len(blob)))
    h = struct.pack("<8s10I16s512s32s",
                    b"ANDROID!", len(kernel), KERNEL_ADDR, len(ramdisk), RAMDISK_ADDR,
                    len(second), SECOND_ADDR, TAGS_ADDR, PAGE, 0, OS_VERSION,
                    b"", cmdline, sha.digest().ljust(32, b"\0"))
    h += b"\0" * 1024                           # extra_cmdline
    img = pad(h, PAGE) + pad(kernel, PAGE) + pad(ramdisk, PAGE) + pad(second, PAGE)
    assert len(img) <= PART_SIZE, f"too big for recovery: {len(img)}"
    open(out, "wb").write(img)
    print(f"{out}: {len(img)} bytes ({len(img) / PART_SIZE:.0%} of the partition)")
    print(f"  kernel {len(kernel)}  ramdisk {len(ramdisk)}  second(RSCE) {len(second)}  dtb {len(fdt)}")
    print(f"  cmdline: {cmdline.decode()}")
    print(f"  sha256: {hashlib.sha256(img).hexdigest()}")


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        sys.exit(__doc__)
    main(*sys.argv[1:])
