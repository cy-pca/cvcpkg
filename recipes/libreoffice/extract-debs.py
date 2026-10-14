#!/usr/bin/env python3
"""recipes/libreoffice/extract-debs.py -- unpack TDF's .deb files into a tree.

TDF ships its Linux build as a tarball of .deb packages. A .deb is an ar(1)
archive whose data.tar.* member holds the files; this reads the ar container
and that member with the standard library only (tarfile handles .xz/.gz/.bz2),
so the build needs neither dpkg nor binutils' ar on the builder.

usage: extract-debs.py DEST DEB...
"""
import io
import sys
import tarfile


def members(deb_path):
    with open(deb_path, "rb") as f:
        data = f.read()
    if data[:8] != b"!<arch>\n":
        raise SystemExit(f"{deb_path}: not an ar archive")
    i = 8
    while i + 60 <= len(data):
        name = data[i : i + 16].decode("ascii").strip().rstrip("/")
        size = int(data[i + 48 : i + 58].decode("ascii").strip())
        yield name, data[i + 60 : i + 60 + size]
        i += 60 + size + (size & 1)  # members are 2-byte aligned


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    dest, debs = sys.argv[1], sys.argv[2:]
    for deb in debs:
        payload = [blob for name, blob in members(deb) if name.startswith("data.tar")]
        if len(payload) != 1:
            raise SystemExit(f"{deb}: expected one data.tar.* member, found {len(payload)}")
        with tarfile.open(fileobj=io.BytesIO(payload[0]), mode="r:*") as tf:
            # Absolute paths and '..' are refused; symlinks are kept as-is
            # (LibreOffice's are relative, within its own tree).
            for m in tf.getmembers():
                if m.name.startswith("/") or ".." in m.name.split("/"):
                    raise SystemExit(f"{deb}: unsafe path {m.name!r}")
            tf.extractall(dest, filter="tar") if hasattr(tarfile, "data_filter") else tf.extractall(dest)
        print(f"extracted {deb.rsplit('/', 1)[-1]}")


if __name__ == "__main__":
    main()
