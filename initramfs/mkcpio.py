#!/usr/bin/env python3
"""Writes a newc cpio of a directory to stdout: every entry owned by root,
plus the device node /dev/console (cannot be created without root)."""
import os, stat, sys

root = sys.argv[1]
out = sys.stdout.buffer
ino = [1]


def entry(name, mode, data=b"", rdev=(0, 0), nlink=1):
    n = name.encode() + b"\0"
    hdr = b"070701" + b"".join(b"%08X" % v for v in (
        ino[0], mode, 0, 0, nlink, 0, len(data), 0, 0, rdev[0], rdev[1], len(n), 0))
    ino[0] += 1
    out.write(hdr + n + b"\0" * (-(110 + len(n)) % 4))
    out.write(data + b"\0" * (-len(data) % 4))


for dirpath, dirs, files in os.walk(root):
    dirs.sort(); files.sort()
    rel = os.path.relpath(dirpath, root)
    if rel != ".":
        entry(rel, stat.S_IFDIR | (os.lstat(dirpath).st_mode & 0o7777), nlink=2)
    for f in files + [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]:
        p = os.path.join(dirpath, f)
        r = os.path.normpath(os.path.join(rel, f))
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            entry(r, stat.S_IFLNK | 0o777, os.readlink(p).encode())
        elif stat.S_ISREG(st.st_mode):
            entry(r, stat.S_IFREG | (st.st_mode & 0o7777), open(p, "rb").read())
entry("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1))
entry("TRAILER!!!", 0)
