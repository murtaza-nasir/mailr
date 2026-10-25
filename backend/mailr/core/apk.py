"""Read an Android APK's identity: the package name, versionCode and versionName from its binary
AndroidManifest.xml, and whether it is signed. Pure Python, no Android tools.

An APK is a zip file. Its AndroidManifest.xml is in Android's binary XML format (AXML): a chunk
header, a string pool, a resource id map, then namespace and element chunks. Only the first
element (<manifest>) is read. Attribute names are matched by their resource id when the map has
one (shrunk builds may blank the names), else by name.

Signing is checked for presence only (an APK Signing Block before the zip's central directory, or
a v1 signature file in META-INF). Android itself verifies the signature at install.
"""

from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import dataclass

# Chunk types (frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h).
RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_START_ELEMENT = 0x0102
RES_XML_RESOURCE_MAP = 0x0180
UTF8_FLAG = 0x100
# Typed values.
TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
# android:versionCode and android:versionName (android.R.attr).
ATTR_VERSION_CODE = 0x0101021B
ATTR_VERSION_NAME = 0x0101021C
SIG_BLOCK_MAGIC = b"APK Sig Block 42"


class ApkError(ValueError):
    """Not an APK, or one whose manifest cannot be read."""


@dataclass(frozen=True)
class ApkInfo:
    package: str
    version_code: int
    version_name: str
    signed: bool


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _strings(b: bytes, start: int) -> list[str]:
    """The string pool chunk at `start`."""
    header = _u16(b, start + 2)
    count, flags, strings_start = _u32(b, start + 8), _u32(b, start + 16), _u32(b, start + 20)
    utf8 = bool(flags & UTF8_FLAG)
    offsets = start + header
    data = start + strings_start
    out: list[str] = []
    for i in range(count):
        o = data + _u32(b, offsets + 4 * i)
        if utf8:
            # Character count, then byte count; each 1 byte, or 2 with the high bit set.
            o += 2 if b[o] & 0x80 else 1
            n = b[o]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | b[o + 1]
                o += 2
            else:
                o += 1
            out.append(b[o:o + n].decode("utf-8", "replace"))
        else:
            n = _u16(b, o)
            o += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | _u16(b, o)
                o += 2
            out.append(b[o:o + 2 * n].decode("utf-16-le", "replace"))
    return out


def parse_manifest(b: bytes) -> tuple[str, int, str]:
    """(package, versionCode, versionName) from a binary AndroidManifest.xml. ApkError."""
    try:
        if len(b) < 8 or _u16(b, 0) != RES_XML:
            raise ApkError("the manifest is not binary Android XML")
        o = _u16(b, 2)
        strings: list[str] = []
        res_ids: list[int] = []
        while o + 8 <= len(b):
            kind, header, size = _u16(b, o), _u16(b, o + 2), _u32(b, o + 4)
            if size < 8:
                raise ApkError("a damaged manifest chunk")
            if kind == RES_STRING_POOL:
                strings = _strings(b, o)
            elif kind == RES_XML_RESOURCE_MAP:
                res_ids = [_u32(b, o + 8 + 4 * i) for i in range((size - 8) // 4)]
            elif kind == RES_XML_START_ELEMENT:
                ext = o + header
                name_i = _u32(b, ext + 4)
                if name_i >= len(strings) or strings[name_i] != "manifest":
                    raise ApkError("the first element is not <manifest>")
                a_start, a_size, a_count = _u16(b, ext + 8), _u16(b, ext + 10), _u16(b, ext + 12)
                package, code, vname = "", None, ""
                for i in range(a_count):
                    a = ext + a_start + i * a_size
                    an, raw = _u32(b, a + 4), _u32(b, a + 8)
                    dtype, data = b[a + 15], _u32(b, a + 16)
                    rid = res_ids[an] if an < len(res_ids) else 0
                    aname = strings[an] if an < len(strings) else ""
                    sval = strings[raw] if raw < len(strings) else (
                        strings[data] if dtype == TYPE_STRING and data < len(strings) else "")
                    if rid == ATTR_VERSION_CODE or (not rid and aname == "versionCode"):
                        if dtype in (TYPE_INT_DEC, TYPE_INT_HEX):
                            code = data
                        elif sval.isdigit():
                            code = int(sval)
                    elif rid == ATTR_VERSION_NAME or (not rid and aname == "versionName"):
                        vname = sval if dtype != TYPE_REFERENCE else ""
                    elif aname == "package" and not rid:
                        package = sval
                if not package:
                    raise ApkError("the manifest names no package")
                if code is None:
                    raise ApkError("the manifest has no versionCode")
                return package, code, vname or str(code)
            o += size
    except (struct.error, IndexError) as e:
        raise ApkError("the manifest is damaged") from e
    raise ApkError("the manifest has no <manifest> element")


def _has_signing_block(data: bytes) -> bool:
    """An APK Signing Block (v2 and later) sits right before the central directory."""
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65_557))
    if eocd < 0:
        return False
    cd = _u32(data, eocd + 16)
    return cd >= 16 and data[cd - 16:cd] == SIG_BLOCK_MAGIC


def read(data: bytes) -> ApkInfo:
    """The identity of an APK given as bytes. ApkError when it is not a usable APK: not a zip, no
    manifest, no classes.dex, or not signed."""
    if not data.startswith(b"PK\x03\x04"):
        raise ApkError("this is not an APK (not a zip file)")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "AndroidManifest.xml" not in names:
                raise ApkError("this is not an APK (no AndroidManifest.xml)")
            if "classes.dex" not in names:
                raise ApkError("this is not an APK (no classes.dex)")
            manifest = z.read("AndroidManifest.xml")
            v1 = any(n.startswith("META-INF/") and n.rsplit(".", 1)[-1].upper() in ("RSA", "DSA", "EC")
                     for n in names)
    except zipfile.BadZipFile as e:
        raise ApkError("this is not an APK (a damaged zip file)") from e
    package, code, name = parse_manifest(manifest)
    signed = v1 or _has_signing_block(data)
    if not signed:
        raise ApkError("the APK is not signed; Android would refuse to install it")
    return ApkInfo(package=package, version_code=code, version_name=name, signed=True)
