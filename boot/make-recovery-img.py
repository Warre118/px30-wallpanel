#!/usr/bin/env python3
"""
Bouwt recovery.img voor de vendor-U-Boot van het Smatek PX30-panel:
een Android-bootimage v0 (zelfde indeling als de originele boot/recovery.img)
met onze mainline-kernel, GEEN ramdisk, en als "second" een Rockchip
RSCE-resource met enkel rk-kernel.dtb.

Gebruik: make-recovery-img.py <Image> <dtb> <cmdline.txt> <uit.img> [initramfs.cpio.gz]
Met initramfs: de PX30-initramfs (initramfs/), voor boot (px30.mode=normal)
en recovery (px30.mode=rescue). Zonder: lege cpio, root= uit de cmdline.
Schrijft niets naar het panel.
"""
import hashlib, struct, sys

PAGE = 2048
BLK = 512
PART_SIZE = 64 << 20          # recovery-partitie = 0x20000 sectoren
# laadadressen zoals in het origineel (vendor-U-Boot gebruikt eigen adressen)
KERNEL_ADDR, RAMDISK_ADDR, SECOND_ADDR, TAGS_ADDR = 0x10008000, 0x11000000, 0x10F00000, 0x10000100
OS_VERSION = 0x10040137       # overgenomen uit de originele recovery.img (Android 8.1)


def pad(b, n):
    return b + b"\0" * (-len(b) % n)


def rsce(files):
    """Rockchip resource: 1 blok header, 1 blok per entry, data blok-uitgelijnd."""
    hdr = struct.pack("<4sHHBBBxI", b"RSCE", 0, 0, 1, 1, 1, len(files))
    hdr = pad(hdr, BLK)
    entries, data = b"", b""
    off = 1 + len(files)                       # in blokken
    for name, content in files:
        e = b"ENTR" + name.encode().ljust(256, b"\0") + struct.pack("<II", off + len(data) // BLK, len(content))
        entries += pad(e, BLK)
        data += pad(content, BLK)
    return hdr + entries + data


def empty_cpio():
    """Minimale initramfs: enkel /dev en de TRAILER, GEEN /init. De kernel pakt
    ze uit, vindt geen /init en mount daarna gewoon root= (prepare_namespace).
    Nodig omdat de vendor-U-Boot een recovery zonder ramdisk niet start."""
    def entry(name, mode, nlink):
        n = name.encode() + b"\0"
        h = b"070701" + b"".join(b"%08X" % v for v in (0, mode, 0, 0, nlink, 0, 0, 0, 0, 0, 0, len(n), 0))
        return pad(h + n, 4)
    return pad(entry("dev", 0o040755, 2) + entry("TRAILER!!!", 0, 1), BLK)


def main(image, dtb, cmdfile, out, initrd=None):
    kernel = open(image, "rb").read()
    fdt = open(dtb, "rb").read()
    cmdline = open(cmdfile).readline().strip().encode()
    assert kernel[0x38:0x3C] == b"ARM\x64", "geen arm64 Image"
    assert fdt[:4] == b"\xd0\x0d\xfe\xed", "geen DTB"
    assert len(cmdline) < 512, "cmdline te lang (max 511)"
    second = rsce([("rk-kernel.dtb", fdt)])
    ramdisk = open(initrd, "rb").read() if initrd else empty_cpio()
    # id = sha1 over (kernel, len, ramdisk, len, second, len) zoals mkbootimg
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
    assert len(img) <= PART_SIZE, f"te groot voor recovery: {len(img)}"
    open(out, "wb").write(img)
    print(f"{out}: {len(img)} bytes ({len(img) / PART_SIZE:.0%} van recovery)")
    print(f"  kernel {len(kernel)}  ramdisk {len(ramdisk)}  second(RSCE) {len(second)}  dtb {len(fdt)}")
    print(f"  cmdline: {cmdline.decode()}")
    print(f"  sha256: {hashlib.sha256(img).hexdigest()}")


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        sys.exit(__doc__)
    main(*sys.argv[1:])
