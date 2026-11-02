"""Minimal checked ELF32 linker for this freestanding ARM7TDMI patch's Clang objects.

Only read-only allocatable sections, ABS32/REL32 and Thumb BL relocations are
supported. Undefined symbols, writable globals and other relocations fail closed.
"""
import struct


def link(objects, base):
    units = []
    sections = []
    symbols = {}
    image = bytearray()
    for data in objects:
        if data[:7] != b"\x7fELF\x01\x01\x01" or struct.unpack_from("<H", data, 18)[0] != 40:
            raise ValueError("Expected ELF32 little-endian ARM object")
        offset = struct.unpack_from("<I", data, 32)[0]
        size, count, names_index = struct.unpack_from("<HHH", data, 46)
        headers = [struct.unpack_from("<10I", data, offset + i * size) for i in range(count)]
        names = headers[names_index]
        strings = data[names[4]:names[4] + names[5]]
        unit = {"data": data, "headers": headers, "mapped": {}, "symbols": {}}
        for i, h in enumerate(headers):
            name = strings[h[0]:].split(b"\0", 1)[0].decode()
            if h[2] & 2 and h[1] != 0x70000001:  # Exclude unused unwind indexes.
                if h[2] & 1 or h[1] != 1:
                    raise ValueError(f"Unexpected writable/non-PROGBITS section: {name}")
                sections.append((name != ".text.entry", unit, i, h))
        units.append(unit)
    for _, unit, i, h in sorted(sections, key=lambda x: x[0]):
        align = max(h[8], 1)
        image.extend(b"\0" * (-len(image) % align))
        unit["mapped"][i] = len(image)
        image.extend(unit["data"][h[4]:h[4] + h[5]])
    for unit in units:
        data, headers = unit["data"], unit["headers"]
        for table_index, h in enumerate(headers):
            if h[1] != 2:
                continue
            names = headers[h[6]]
            strings = data[names[4]:names[4] + names[5]]
            table = []
            for offset in range(h[4], h[4] + h[5], 16):
                name, value, _, info, _, section = struct.unpack_from("<IIIBBH", data, offset)
                name = strings[name:].split(b"\0", 1)[0].decode()
                address = base + unit["mapped"][section] + value if section in unit["mapped"] else None
                if info >> 4 == 1 and address is not None:
                    if name in symbols:
                        raise ValueError(f"Duplicate symbol: {name}")
                    symbols[name] = address
                table.append((name, address))
            unit["symbols"][table_index] = table
    for unit in units:
        data = unit["data"]
        for h in unit["headers"]:
            if h[1] != 9 or h[7] not in unit["mapped"]:
                continue
            table = unit["symbols"][h[6]]
            for offset in range(h[4], h[4] + h[5], 8):
                place, info = struct.unpack_from("<II", data, offset)
                name, target = table[info >> 8]
                if target is None:
                    target = symbols.get(name)
                if target is None:
                    raise ValueError(f"Undefined symbol: {name}")
                index = unit["mapped"][h[7]] + place
                kind = info & 255
                if kind in (2, 3):
                    value = struct.unpack_from("<I", image, index)[0] + target
                    if kind == 3:
                        value -= base + index
                    struct.pack_into("<I", image, index, value & 0xFFFFFFFF)
                elif kind == 10:
                    high, low = struct.unpack_from("<HH", image, index)
                    if high & 0xF800 != 0xF000 or low & 0xF800 != 0xF800:
                        raise ValueError("Expected ARMv4T Thumb BL instruction")
                    addend = ((high & 0x7FF) << 12) | ((low & 0x7FF) << 1)
                    if addend & 0x400000:
                        addend -= 0x800000
                    delta = (target & ~1) + addend - (base + index)
                    if delta % 2 or not -(1 << 22) <= delta < (1 << 22):
                        raise ValueError("Thumb BL relocation out of range")
                    struct.pack_into("<HH", image, index, 0xF000 | ((delta >> 12) & 0x7FF),
                                     0xF800 | ((delta >> 1) & 0x7FF))
                else:
                    raise ValueError(f"Unsupported ARM relocation: {kind}")
    return bytes(image), symbols
