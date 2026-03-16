#!/usr/bin/env python3
"""
Manual APK builder for simple WebView Android apps.
Generates binary AndroidManifest.xml, resources.arsc, classes.dex,
packages them into a ZIP (APK), and signs with jarsigner.
"""

import struct
import hashlib
import zipfile
import os
import subprocess
import sys
import zlib

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_DIR, "app", "src", "main")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")

PACKAGE_NAME = "com.chompy.game"
APP_LABEL = "Chompy"
ACTIVITY_NAME = ".MainActivity"
MIN_SDK = 21
TARGET_SDK = 23
VERSION_CODE = 1
VERSION_NAME = "1.0"


# ===== Binary XML (AXML) Builder =====

class AXMLWriter:
    """Builds Android Binary XML format."""

    # Chunk types
    CHUNK_STRINGPOOL = 0x001C0001
    CHUNK_RESOURCEMAP = 0x00080180
    CHUNK_STARTNAMESPACE = 0x00100100
    CHUNK_ENDNAMESPACE = 0x00100101
    CHUNK_STARTELEMENT = 0x00100102
    CHUNK_ENDELEMENT = 0x00100103
    CHUNK_XML = 0x00080003

    # Android resource IDs for manifest attributes
    ATTR_IDS = {
        "versionCode": 0x0101021b,
        "versionName": 0x0101021c,
        "minSdkVersion": 0x0101020c,
        "targetSdkVersion": 0x01010270,
        "label": 0x01010001,
        "icon": 0x01010002,
        "name": 0x01010003,
        "theme": 0x01010000,
        "hardwareAccelerated": 0x010102d3,
        "exported": 0x01010010,
        "configChanges": 0x0101001f,
        "screenOrientation": 0x0101001e,
        "package": 0xffffffff,  # special
    }

    # Value types
    TYPE_NULL = 0x00
    TYPE_REFERENCE = 0x01
    TYPE_STRING = 0x03
    TYPE_INT_DEC = 0x10
    TYPE_INT_HEX = 0x11
    TYPE_INT_BOOLEAN = 0x12

    def __init__(self):
        self.strings = []
        self.string_map = {}
        self.resource_ids = []
        self.chunks = []

    def add_string(self, s):
        if s in self.string_map:
            return self.string_map[s]
        idx = len(self.strings)
        self.strings.append(s)
        self.string_map[s] = idx
        return idx

    def _encode_utf16_string(self, s):
        encoded = s.encode('utf-16-le')
        # String format: u16 char_count, u16 char_count(again for utf16), utf16 data, u16 null
        return struct.pack('<HH', len(s), len(s)) + encoded + b'\x00\x00'

    def build_string_pool(self):
        # Encode all strings
        encoded_strings = []
        for s in self.strings:
            encoded_strings.append(self._encode_utf16_string(s))

        # Calculate offsets
        offsets = []
        offset = 0
        for es in encoded_strings:
            offsets.append(offset)
            offset += len(es)

        string_data = b''.join(encoded_strings)

        # String pool header
        str_count = len(self.strings)
        style_count = 0
        flags = 0  # UTF-16
        strings_start = 28 + str_count * 4  # header + offsets
        styles_start = 0

        header = struct.pack('<IIIIIII',
            self.CHUNK_STRINGPOOL,
            strings_start + len(string_data),
            str_count,
            style_count,
            flags,
            strings_start,
            styles_start,
        )

        offset_data = b''.join(struct.pack('<I', o) for o in offsets)

        return header + offset_data + string_data

    def build_resource_map(self):
        if not self.resource_ids:
            return b''
        data = b''.join(struct.pack('<I', rid) for rid in self.resource_ids)
        header = struct.pack('<II',
            self.CHUNK_RESOURCEMAP,
            8 + len(data),
        )
        return header + data

    def build(self):
        """Build the complete AXML binary."""
        # Pre-populate strings in the right order
        # First: namespace strings, then attribute names, then values

        android_ns = "http://schemas.android.com/apk/res/android"
        ns_prefix = "android"

        # Attribute names that map to resource IDs (must be first in string pool after ns)
        attr_names_ordered = [
            "versionCode", "versionName", "minSdkVersion", "targetSdkVersion",
            "label", "icon", "name", "theme", "hardwareAccelerated",
            "exported", "configChanges", "screenOrientation", "package",
        ]

        # Pre-add namespace strings
        self.add_string(android_ns)
        self.add_string(ns_prefix)

        # Pre-add attribute names
        for name in attr_names_ordered:
            self.add_string(name)

        # Pre-add all value strings
        self.add_string(PACKAGE_NAME)  # com.chompy.game
        self.add_string(VERSION_NAME)  # 1.0
        self.add_string(APP_LABEL)     # Chompy
        self.add_string("@android:style/Theme.NoTitleBar.Fullscreen")
        self.add_string(ACTIVITY_NAME) # .MainActivity
        self.add_string("android.intent.action.MAIN")
        self.add_string("android.intent.category.LAUNCHER")
        self.add_string("")  # empty string for elements without ns

        # Element names
        self.add_string("manifest")
        self.add_string("uses-sdk")
        self.add_string("application")
        self.add_string("activity")
        self.add_string("intent-filter")
        self.add_string("action")
        self.add_string("category")

        # Resource IDs for attribute names (must match order of attr name strings)
        self.resource_ids = [self.ATTR_IDS.get(name, 0) for name in attr_names_ordered]

        # Build string pool and resource map
        string_pool = self.build_string_pool()
        resource_map = self.build_resource_map()

        # Now build XML chunks
        xml_chunks = []

        # Start namespace
        ns_uri_idx = self.string_map[android_ns]
        ns_prefix_idx = self.string_map[ns_prefix]
        xml_chunks.append(self._namespace_chunk(self.CHUNK_STARTNAMESPACE, ns_prefix_idx, ns_uri_idx, 1))

        # <manifest>
        xml_chunks.append(self._start_element("manifest", [
            (android_ns, "package", PACKAGE_NAME, self.TYPE_STRING),
            (android_ns, "versionCode", VERSION_CODE, self.TYPE_INT_DEC),
            (android_ns, "versionName", VERSION_NAME, self.TYPE_STRING),
        ], 2))

        # <uses-sdk>
        xml_chunks.append(self._start_element("uses-sdk", [
            (android_ns, "minSdkVersion", MIN_SDK, self.TYPE_INT_DEC),
            (android_ns, "targetSdkVersion", TARGET_SDK, self.TYPE_INT_DEC),
        ], 3))
        xml_chunks.append(self._end_element("uses-sdk", 3))

        # <application>
        xml_chunks.append(self._start_element("application", [
            (android_ns, "label", APP_LABEL, self.TYPE_STRING),
            (android_ns, "theme", 0x01030007, self.TYPE_REFERENCE),  # Theme.NoTitleBar.Fullscreen
            (android_ns, "hardwareAccelerated", 0xFFFFFFFF, self.TYPE_INT_BOOLEAN),
            (android_ns, "icon", 0x7f020000, self.TYPE_REFERENCE),  # @drawable/ic_launcher
        ], 4))

        # <activity>
        # configChanges: orientation|screenSize|keyboardHidden = 0x000000A0 | 0x00000400 | 0x00000020 = 0x04A0
        config_changes = 0x00000080 | 0x00000400 | 0x00000020  # orientation|screenSize|keyboardHidden
        xml_chunks.append(self._start_element("activity", [
            (android_ns, "name", ACTIVITY_NAME, self.TYPE_STRING),
            (android_ns, "exported", 0xFFFFFFFF, self.TYPE_INT_BOOLEAN),
            (android_ns, "configChanges", config_changes, self.TYPE_INT_HEX),
            (android_ns, "screenOrientation", 1, self.TYPE_INT_DEC),  # portrait
        ], 5))

        # <intent-filter>
        xml_chunks.append(self._start_element("intent-filter", [], 6))

        # <action>
        xml_chunks.append(self._start_element("action", [
            (android_ns, "name", "android.intent.action.MAIN", self.TYPE_STRING),
        ], 7))
        xml_chunks.append(self._end_element("action", 7))

        # <category>
        xml_chunks.append(self._start_element("category", [
            (android_ns, "name", "android.intent.category.LAUNCHER", self.TYPE_STRING),
        ], 8))
        xml_chunks.append(self._end_element("category", 8))

        xml_chunks.append(self._end_element("intent-filter", 6))
        xml_chunks.append(self._end_element("activity", 5))
        xml_chunks.append(self._end_element("application", 4))
        xml_chunks.append(self._end_element("manifest", 2))

        # End namespace
        xml_chunks.append(self._namespace_chunk(self.CHUNK_ENDNAMESPACE, ns_prefix_idx, ns_uri_idx, 9))

        # Combine everything
        body = string_pool + resource_map + b''.join(xml_chunks)

        # XML document header
        header = struct.pack('<II',
            self.CHUNK_XML,
            8 + len(body),
        )

        return header + body

    def _namespace_chunk(self, chunk_type, prefix_idx, uri_idx, line):
        return struct.pack('<IIIIII',
            chunk_type,
            24,  # chunk size
            line,
            0xFFFFFFFF,  # comment
            prefix_idx,
            uri_idx,
        )

    def _start_element(self, name, attrs, line):
        name_idx = self.string_map[name]
        ns_idx = 0xFFFFFFFF  # no namespace for element name

        attr_data = b''
        for ns_uri, attr_name, value, value_type in attrs:
            attr_ns_idx = self.string_map.get(ns_uri, 0xFFFFFFFF)
            attr_name_idx = self.string_map[attr_name]

            if value_type == self.TYPE_STRING:
                if isinstance(value, str):
                    raw_value_idx = self.string_map.get(value, self.add_string(value))
                    typed_value = struct.pack('<HBxI', 8, self.TYPE_STRING, raw_value_idx)
                    attr_data += struct.pack('<IIi', attr_ns_idx, attr_name_idx, raw_value_idx)
                    attr_data += typed_value
                else:
                    attr_data += struct.pack('<IIi', attr_ns_idx, attr_name_idx, -1)
                    attr_data += struct.pack('<HBxI', 8, value_type, value)
            elif value_type == self.TYPE_REFERENCE:
                attr_data += struct.pack('<IIi', attr_ns_idx, attr_name_idx, -1)
                attr_data += struct.pack('<HBxI', 8, self.TYPE_REFERENCE, value)
            elif value_type in (self.TYPE_INT_DEC, self.TYPE_INT_HEX):
                attr_data += struct.pack('<IIi', attr_ns_idx, attr_name_idx, -1)
                attr_data += struct.pack('<HBxI', 8, value_type, value)
            elif value_type == self.TYPE_INT_BOOLEAN:
                attr_data += struct.pack('<IIi', attr_ns_idx, attr_name_idx, -1)
                attr_data += struct.pack('<HBxI', 8, self.TYPE_INT_BOOLEAN, value)

        attr_count = len(attrs)
        # header: type(4) + size(4) + lineNumber(4) + comment(4) + nsIdx(4) + nameIdx(4)
        # + attrStart(2) + attrSize(2) + attrCount(2) + idIndex(2) + classIndex(2) + styleIndex(2)
        chunk_size = 36 + len(attr_data)

        header = struct.pack('<IIIIII',
            self.CHUNK_STARTELEMENT,
            chunk_size,
            line,
            0xFFFFFFFF,
            ns_idx,
            name_idx,
        )
        attr_header = struct.pack('<HHHHHH',
            20,  # attribute start (offset from start of attributes section)
            20,  # attribute size (each attr is 20 bytes)
            attr_count,
            0,  # idIndex
            0,  # classIndex
            0,  # styleIndex
        )

        return header + attr_header + attr_data

    def _end_element(self, name, line):
        name_idx = self.string_map[name]
        return struct.pack('<IIIIII',
            self.CHUNK_ENDELEMENT,
            24,
            line,
            0xFFFFFFFF,
            0xFFFFFFFF,  # ns
            name_idx,
        )


# ===== resources.arsc Builder =====

def build_resources_arsc():
    """Build a minimal resources.arsc with drawable/ic_launcher and string/app_name."""
    # We need:
    # - Package: com.chompy.game
    # - Type: drawable (id 0x02) with ic_launcher
    # - Type: string (id 0x03) with app_name = "Chompy"
    # Resource IDs:
    # 0x7f020000 = drawable/ic_launcher
    # 0x7f030000 = string/app_name

    package_name = PACKAGE_NAME

    # Global string pool: contains all value strings and resource names
    strings = ["res/drawable/ic_launcher.png", APP_LABEL]

    # Type strings (names of resource types)
    type_strings = ["attr", "drawable", "string"]

    # Key strings (names of resource entries)
    key_strings = ["ic_launcher", "app_name"]

    def encode_string_pool(strs, utf8=False):
        """Encode a string pool chunk."""
        encoded = []
        for s in strs:
            if utf8:
                utf8_bytes = s.encode('utf-8')
                # UTF-8 format: u8 len, u8 len, utf8 data, u8 null
                if len(s) > 127:
                    char_len = bytes([(len(s) >> 8) | 0x80, len(s) & 0xFF])
                else:
                    char_len = bytes([len(s)])
                if len(utf8_bytes) > 127:
                    byte_len = bytes([(len(utf8_bytes) >> 8) | 0x80, len(utf8_bytes) & 0xFF])
                else:
                    byte_len = bytes([len(utf8_bytes)])
                encoded.append(char_len + byte_len + utf8_bytes + b'\x00')
            else:
                utf16 = s.encode('utf-16-le')
                encoded.append(struct.pack('<HH', len(s), len(s)) + utf16 + b'\x00\x00')

        offsets = []
        off = 0
        for e in encoded:
            offsets.append(off)
            off += len(e)

        string_data = b''.join(encoded)
        offset_data = b''.join(struct.pack('<I', o) for o in offsets)

        flags = 0x100 if utf8 else 0  # UTF8_FLAG
        strings_start = 28 + len(strs) * 4
        chunk_size = strings_start + len(string_data)

        # Align to 4 bytes
        padding = (4 - (chunk_size % 4)) % 4
        string_data += b'\x00' * padding
        chunk_size += padding

        header = struct.pack('<IIIIIII',
            0x001C0001,  # RES_STRING_POOL_TYPE
            chunk_size,
            len(strs),
            0,  # styleCount
            flags,
            strings_start,
            0,  # stylesStart
        )
        return header + offset_data + string_data

    # Build global string pool
    global_pool = encode_string_pool(strings)

    # Build package chunk
    # Package header
    pkg_id = 0x7f

    # Type string pool
    type_pool = encode_string_pool(type_strings, utf8=True)

    # Key string pool
    key_pool = encode_string_pool(key_strings, utf8=True)

    # Type spec for drawable (type id = 2)
    drawable_spec = struct.pack('<IIHHI',
        0x00080202,  # RES_TABLE_TYPE_SPEC_TYPE
        16 + 4,  # chunk size (header + 1 entry * 4)
        2,  # type id (drawable = 2, 1-indexed)
        0,  # res0 + res1
        1,  # entry count
    ) + struct.pack('<I', 0)  # config flags for entry 0

    # Type chunk for drawable
    # Entry: ic_launcher -> res/drawable/ic_launcher.png (string index 0 in global pool)
    entry_data = struct.pack('<HHI',
        8,  # size of entry
        0,  # flags
        0,  # key (index into key strings)
    ) + struct.pack('<HBxI',
        8,  # size of value
        0x03,  # TYPE_STRING
        0,  # string index in global pool (res/drawable/ic_launcher.png)
    )

    config = b'\x00' * 64  # default config (64 bytes of zeros for size + padding)
    config = struct.pack('<I', 64) + b'\x00' * 60  # config size = 64

    type_header_size = 68 + len(config)  # actually it's fixed
    entries_start = 68 + len(config)  # after header + config
    # Wait, let me recalculate. Type chunk header:
    # type(4) + size(4) + id(1) + res0(1) + res1(2) + entryCount(4) + entriesStart(4) + config(64)
    # = 4+4+1+1+2+4+4+64 = 84
    # Then offset array: 1 entry * 4 = 4
    # Then entries data

    offsets_data = struct.pack('<I', 0)  # entry 0 at offset 0

    entries_start_val = 84 + len(offsets_data)
    drawable_type = struct.pack('<IIBBHII',
        0x00080201,  # RES_TABLE_TYPE_TYPE
        entries_start_val + len(entry_data),  # chunk size
        2,  # type id
        0,  # res0
        0,  # res1
        1,  # entry count
        entries_start_val,  # entries start
    ) + config + offsets_data + entry_data

    # Type spec for string (type id = 3)
    string_spec = struct.pack('<IIHHI',
        0x00080202,
        16 + 4,
        3,  # type id (string = 3)
        0,
        1,  # entry count
    ) + struct.pack('<I', 0)

    # Type chunk for string
    entry_data2 = struct.pack('<HHI',
        8,  # size
        0,  # flags
        1,  # key index (app_name in key strings)
    ) + struct.pack('<HBxI',
        8,
        0x03,  # TYPE_STRING
        1,  # string index in global pool ("Chompy")
    )

    offsets_data2 = struct.pack('<I', 0)
    entries_start_val2 = 84 + len(offsets_data2)
    string_type = struct.pack('<IIBBHII',
        0x00080201,
        entries_start_val2 + len(entry_data2),
        3,  # type id
        0,
        0,
        1,
        entries_start_val2,
    ) + config + offsets_data2 + entry_data2

    # Package name as UTF-16 (128 u16 chars = 256 bytes)
    pkg_name_utf16 = package_name.encode('utf-16-le')
    pkg_name_padded = pkg_name_utf16 + b'\x00' * (256 - len(pkg_name_utf16))

    # Package chunk body
    pkg_body = pkg_name_padded
    # typeStrings offset (from start of package chunk header)
    # keyStrings offset
    # Then type_pool, key_pool, specs, types

    type_strings_offset = 288  # 4+4+4+256+4+4+4+4 = 284... let me calculate properly
    # Package header: type(4) + size(4) + id(4) + name(256) + typeStrings(4) + lastPublicType(4) + keyStrings(4) + lastPublicKey(4) = 284
    pkg_header_size = 288  # includes padding

    # Type strings start right after package header
    type_strings_off = pkg_header_size
    key_strings_off = type_strings_off + len(type_pool)

    pkg_content = type_pool + key_pool + drawable_spec + drawable_type + string_spec + string_type
    pkg_chunk_size = pkg_header_size + len(pkg_content)

    pkg_header = struct.pack('<IIII',
        0x00080200,  # RES_TABLE_PACKAGE_TYPE
        pkg_chunk_size,
        pkg_id,
        0,  # padding for name alignment
    )
    # Actually the header format is:
    # u32 type, u32 size, u32 id, u16[128] name, u32 typeStrings, u32 lastPublicType, u32 keyStrings, u32 lastPublicKey
    # = 4 + 4 + 4 + 256 + 4 + 4 + 4 + 4 = 284

    # Let me just carefully pack this
    pkg_header = struct.pack('<III',
        0x00080200,  # type
        284 + len(pkg_content),  # size
        pkg_id,  # id
    )
    pkg_header += pkg_name_padded  # 256 bytes
    pkg_header += struct.pack('<IIII',
        284,  # typeStrings offset (from start of pkg chunk)
        len(type_strings),  # lastPublicType
        284 + len(type_pool),  # keyStrings offset
        len(key_strings),  # lastPublicKey
    )

    assert len(pkg_header) == 284, f"Package header size is {len(pkg_header)}, expected 284"

    package_chunk = pkg_header + pkg_content

    # Table header
    table_body = global_pool + package_chunk
    table_header = struct.pack('<III',
        0x00080002,  # RES_TABLE_TYPE
        12 + len(table_body),  # size
        1,  # package count
    )

    return table_header + table_body


# ===== DEX Builder =====

def build_classes_dex():
    """
    Build a minimal classes.dex for a WebView Activity.
    This is a simplified DEX that creates the necessary class structure.
    Since building DEX from scratch is extremely complex, we'll compile
    the Java and use the existing tools, or create a minimal stub.
    """
    # Actually, for the Chompy app, the Java code is identical to GlowPop
    # except for the package name. We can compile with javac and then
    # manually create the DEX, or we can patch the existing GlowPop DEX.

    # Strategy: Take GlowPop's classes.dex and patch all string references.
    # The DEX string table stores MUTF-8 strings with length prefixes.
    # We need to find and replace:
    # - "Lcom/glowpop/game/MainActivity;" -> "Lcom/chompy/game/MainActivity;"
    # - "Lcom/glowpop/game/R$attr;" -> "Lcom/chompy/game/R$attr;"
    # - "Lcom/glowpop/game/R$drawable;" -> "Lcom/chompy/game/R$drawable;"
    # - "Lcom/glowpop/game/R$string;" -> "Lcom/chompy/game/R$string;"
    # - "Lcom/glowpop/game/R;" -> "Lcom/chompy/game/R;"

    # Since "glowpop" (7) vs "chompy" (6) differ by 1 char, string lengths change.
    # We need to properly update the DEX string table.

    glowpop_dex = os.path.join(PROJECT_DIR, "..", "GlowPop", "glowpop.apk")

    import zipfile as zf
    with zf.ZipFile(glowpop_dex, 'r') as z:
        dex_data = bytearray(z.read('classes.dex'))

    # Parse DEX header to find string table
    magic = dex_data[:8]
    assert magic[:4] == b'dex\n', "Not a DEX file"

    # DEX header fields
    checksum = struct.unpack_from('<I', dex_data, 8)[0]
    signature = dex_data[12:32]
    file_size = struct.unpack_from('<I', dex_data, 32)[0]
    header_size = struct.unpack_from('<I', dex_data, 36)[0]
    endian_tag = struct.unpack_from('<I', dex_data, 40)[0]

    string_ids_size = struct.unpack_from('<I', dex_data, 56)[0]
    string_ids_off = struct.unpack_from('<I', dex_data, 60)[0]

    print(f"DEX: {string_ids_size} strings at offset {string_ids_off}")
    print(f"File size: {file_size}")

    # Read all string offsets
    string_offsets = []
    for i in range(string_ids_size):
        off = struct.unpack_from('<I', dex_data, string_ids_off + i * 4)[0]
        string_offsets.append(off)

    # Read all strings (MUTF-8 format)
    strings_list = []
    for off in string_offsets:
        # Read ULEB128 length
        pos = off
        str_len = 0
        shift = 0
        while True:
            b = dex_data[pos]
            str_len |= (b & 0x7f) << shift
            pos += 1
            if (b & 0x80) == 0:
                break
            shift += 7

        # Read MUTF-8 string until null
        start = pos
        while dex_data[pos] != 0:
            pos += 1
        s = dex_data[start:pos].decode('utf-8', errors='replace')
        strings_list.append((off, s))

    # Find strings to replace
    replacements = {}
    for off, s in strings_list:
        new_s = s.replace('glowpop', 'chompy').replace('GlowPop', 'Chompy').replace('Glow Pop', 'Chompy')
        if new_s != s:
            replacements[off] = (s, new_s)
            print(f"  Replace: '{s}' -> '{new_s}'")

    # Since string lengths change, we need to rebuild the DEX data section.
    # This is complex, so instead let's use a different approach:
    # Rebuild the entire string data area and adjust all offsets.

    # Collect all string entries with their new values
    new_strings = []
    for i, (off, s) in enumerate(strings_list):
        if off in replacements:
            new_strings.append(replacements[off][1])
        else:
            new_strings.append(s)

    # Find the data area that contains strings (from first string offset to end of last string)
    data_start = min(string_offsets)

    # Encode new strings and build new string data
    new_string_data = bytearray()
    new_string_offsets = []

    for s in new_strings:
        new_off = data_start + len(new_string_data)
        new_string_offsets.append(new_off)

        # Encode ULEB128 length
        str_len = len(s)
        uleb = []
        while str_len > 0x7f:
            uleb.append((str_len & 0x7f) | 0x80)
            str_len >>= 7
        uleb.append(str_len & 0x7f)

        new_string_data.extend(uleb)
        new_string_data.extend(s.encode('utf-8'))
        new_string_data.append(0)  # null terminator

    # Find end of original string data
    last_off = max(string_offsets)
    pos = last_off
    # Skip ULEB128
    while dex_data[pos] & 0x80:
        pos += 1
    pos += 1
    # Skip string content
    while dex_data[pos] != 0:
        pos += 1
    pos += 1  # null terminator
    old_string_data_end = pos

    old_string_data_len = old_string_data_end - data_start
    new_string_data_len = len(new_string_data)
    size_diff = new_string_data_len - old_string_data_len

    print(f"String data: old={old_string_data_len}, new={new_string_data_len}, diff={size_diff}")

    # Build new DEX
    new_dex = bytearray()
    new_dex.extend(dex_data[:data_start])  # everything before string data
    new_dex.extend(new_string_data)        # new string data
    new_dex.extend(dex_data[old_string_data_end:])  # everything after

    # Update string ID offsets
    for i, new_off in enumerate(new_string_offsets):
        struct.pack_into('<I', new_dex, string_ids_off + i * 4, new_off)

    # Update file size
    struct.pack_into('<I', new_dex, 32, len(new_dex))

    # Update all section offsets that are >= data_start + old_string_data_len
    # These sections might have shifted. Let's update map offsets.
    if size_diff != 0:
        # We need to adjust offsets for sections that come after the string data
        # Read relevant header fields and adjust

        # type_ids_off
        for field_off in [68, 76, 84, 92, 100, 108]:  # type, proto, field, method, class, data offsets
            val = struct.unpack_from('<I', new_dex, field_off)[0]
            if val >= old_string_data_end:
                struct.pack_into('<I', new_dex, field_off, val + size_diff)

        # Also need to adjust data_off and data_size
        data_size_off = 104
        data_off_off = 108
        # map_off
        map_off_off = 52
        map_off = struct.unpack_from('<I', new_dex, map_off_off)[0]
        if map_off >= old_string_data_end:
            struct.pack_into('<I', new_dex, map_off_off, map_off + size_diff)

        # Adjust all offsets in the map list if it exists
        new_map_off = struct.unpack_from('<I', new_dex, map_off_off)[0]
        if new_map_off > 0 and new_map_off < len(new_dex) - 4:
            map_size = struct.unpack_from('<I', new_dex, new_map_off)[0]
            for i in range(map_size):
                entry_off = new_map_off + 4 + i * 12
                if entry_off + 12 <= len(new_dex):
                    item_type = struct.unpack_from('<H', new_dex, entry_off)[0]
                    item_offset = struct.unpack_from('<I', new_dex, entry_off + 8)[0]
                    if item_offset >= old_string_data_end:
                        struct.pack_into('<I', new_dex, entry_off + 8, item_offset + size_diff)

        # Adjust annotation and other data offsets within class_defs
        # For a simple app like this, the key offsets are in the header

    # Recalculate SHA-1 signature (bytes 12-31)
    sha1 = hashlib.sha1(bytes(new_dex[32:])).digest()
    new_dex[12:32] = sha1

    # Recalculate Adler32 checksum (bytes 8-11)
    checksum = zlib.adler32(bytes(new_dex[12:])) & 0xFFFFFFFF
    struct.pack_into('<I', new_dex, 8, checksum)

    return bytes(new_dex)


# ===== APK Assembly =====

def build_apk():
    print("=== CHOMPY APK BUILD (Manual) ===")

    os.makedirs(BUILD_DIR, exist_ok=True)

    # Step 1: Build AndroidManifest.xml
    print("[1/5] Building AndroidManifest.xml...")
    axml = AXMLWriter()
    manifest_data = axml.build()

    # Step 2: Build resources.arsc
    print("[2/5] Building resources.arsc...")
    resources_data = build_resources_arsc()

    # Step 3: Build classes.dex (patch from GlowPop)
    print("[3/5] Building classes.dex...")
    dex_data = build_classes_dex()

    # Step 4: Create APK (ZIP)
    print("[4/5] Packaging APK...")
    apk_path = os.path.join(BUILD_DIR, "chompy-unsigned.apk")
    with zipfile.ZipFile(apk_path, 'w', zipfile.ZIP_DEFLATED) as apk:
        apk.writestr("AndroidManifest.xml", manifest_data)
        apk.writestr("classes.dex", dex_data)
        apk.writestr("resources.arsc", resources_data)

        # Add game.html from assets
        game_html = os.path.join(SRC_DIR, "assets", "game.html")
        apk.write(game_html, "assets/game.html")

        # Add icon
        icon_path = os.path.join(SRC_DIR, "res", "drawable", "ic_launcher.png")
        apk.write(icon_path, "res/drawable/ic_launcher.png")

    # Step 5: Sign APK
    print("[5/5] Signing APK...")
    keystore = os.path.join(PROJECT_DIR, "debug.keystore")
    if not os.path.exists(keystore):
        subprocess.run([
            "keytool", "-genkey", "-v",
            "-keystore", keystore,
            "-storepass", "android",
            "-alias", "androiddebugkey",
            "-keypass", "android",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-dname", "CN=Debug, OU=Debug, O=Debug, L=Debug, ST=Debug, C=US",
        ], check=True, capture_output=True)

    result = subprocess.run([
        "jarsigner", "-verbose",
        "-sigalg", "SHA256withRSA",
        "-digestalg", "SHA-256",
        "-keystore", keystore,
        "-storepass", "android",
        "-keypass", "android",
        apk_path,
        "androiddebugkey",
    ], check=True, capture_output=True, text=True)

    # Copy final APK
    final_apk = os.path.join(PROJECT_DIR, "chompy.apk")
    import shutil
    shutil.copy2(apk_path, final_apk)

    size = os.path.getsize(final_apk)
    print(f"\n=== BUILD COMPLETE ===")
    print(f"APK: {final_apk}")
    print(f"Size: {size / 1024:.1f}K")

    return final_apk


if __name__ == "__main__":
    build_apk()
