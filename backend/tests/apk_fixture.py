"""A tiny APK for tests: a zip with a binary AndroidManifest.xml (Android's AXML, written here the
way aapt2 writes it), a classes.dex and a v1 signature file. Nothing in it runs; it is enough for
core/apk.py to read the package and version, and for the publish checks."""

from __future__ import annotations

import io
import struct
import zipfile

ANDROID_NS = "http://schemas.android.com/apk/res/android"
VERSION_CODE, VERSION_NAME = 0x0101021B, 0x0101021C


def _pool(strings: list[str], utf8: bool) -> bytes:
    data = b""
    offsets = []
    for s in strings:
        offsets.append(len(data))
        if utf8:
            enc = s.encode("utf-8")
            data += bytes([len(s), len(enc)]) + enc + b"\x00"
        else:
            data += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00"
    while len(data) % 4:
        data += b"\x00"
    header = 28
    start = header + 4 * len(strings)
    body = b"".join(struct.pack("<I", o) for o in offsets) + data
    return struct.pack("<HHIIIIII", 0x0001, header, header + len(body), len(strings), 0,
                       0x100 if utf8 else 0, start, 0) + body


def manifest(package: str, version_code: int, version_name: str, *, utf8: bool = False,
             blank_names: bool = False) -> bytes:
    """A binary manifest with <manifest package=… android:versionCode=… android:versionName=…>.
    `blank_names` empties the attribute names, as a shrunk build may: then only the resource map
    says which attribute is which."""
    strings = ["" if blank_names else "versionCode", "" if blank_names else "versionName", "package",
               "manifest", "android", ANDROID_NS, package, version_name]
    res_map = struct.pack("<HHI", 0x0180, 8, 8 + 8) + struct.pack("<II", VERSION_CODE, VERSION_NAME)
    ns_chunk = struct.pack("<HHIIIII", 0x0100, 16, 24, 1, 0xFFFFFFFF, 4, 5)

    def attr(ns: int, name: int, raw: int, dtype: int, data: int) -> bytes:
        return struct.pack("<IIIHBBI", ns, name, raw, 8, 0, dtype, data)

    attrs = (attr(5, 0, 0xFFFFFFFF, 0x10, version_code) + attr(5, 1, 7, 0x03, 7)
             + attr(0xFFFFFFFF, 2, 6, 0x03, 6))
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 3, 20, 20, 3, 0, 0, 0)
    start = struct.pack("<HHIII", 0x0102, 16, 16 + len(ext) + len(attrs), 1, 0xFFFFFFFF) + ext + attrs
    end = struct.pack("<HHIIIII", 0x0103, 16, 24, 1, 0xFFFFFFFF, 0xFFFFFFFF, 3)
    body = _pool(strings, utf8) + res_map + ns_chunk + start + end
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def build(package: str = "io.github.murtazanasir.mailr", version_code: int = 37, version_name: str = "0.6.2", *,
          signed: bool = True, dex: bool = True, raw_manifest: bytes | None = None, utf8: bool = False,
          blank_names: bool = False, padding: bytes = b"") -> bytes:
    """The APK as bytes. `padding` adds a file so two builds of one version differ."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", raw_manifest if raw_manifest is not None
                   else manifest(package, version_code, version_name, utf8=utf8, blank_names=blank_names))
        if dex:
            z.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 64)
        if signed:
            z.writestr("META-INF/CERT.SF", b"Signature-Version: 1.0\n")
            z.writestr("META-INF/CERT.RSA", b"\x30\x82 test")
        if padding:
            z.writestr("assets/build.txt", padding)
    return buf.getvalue()
