"""Shared headless emulator harness using an installed mGBA libretro core (ctypes only).

The emulator receives ordinary controller input. No game RAM is modified.
ABI reference: https://github.com/libretro/libretro-common/blob/master/include/libretro.h
"""

import ctypes as C
import json
import os
from pathlib import Path
import struct
import zlib

DEFAULT_CORE = Path('C:/RetroBat/emulators/retroarch/cores/mgba_libretro.dll')
BUTTONS = {"B": 0, "Select": 2, "Start": 3, "Up": 4, "Down": 5,
           "Left": 6, "Right": 7, "A": 8, "L": 10, "R": 11}


class Descriptor(C.Structure):
    _fields_ = [("flags", C.c_uint64), ("ptr", C.c_void_p),
                ("offset", C.c_size_t), ("start", C.c_size_t),
                ("select", C.c_size_t), ("disconnect", C.c_size_t),
                ("len", C.c_size_t), ("addrspace", C.c_char_p)]


class MemoryMap(C.Structure):
    _fields_ = [("descriptors", C.POINTER(Descriptor)), ("count", C.c_uint)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class Game(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p),
                ("size", C.c_size_t), ("meta", C.c_char_p)]


class Emulator:
    def __init__(self, core, rom, artifacts, rom_bytes=None):
        self.artifacts = artifacts
        artifacts.mkdir(parents=True, exist_ok=True)
        self.directory = str(artifacts.resolve()).encode()
        self.options = {}
        self.maps = []
        self.mask = 0
        self.frames = 0
        self.pixel_format = 0
        self.video = None
        self.trace = []
        if os.name == "nt":
            self.dll_directory = os.add_dll_directory(str(core.parent))
        self.lib = C.CDLL(str(core))
        env_type = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
        video_type = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
        audio_type = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
        batch_type = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
        poll_type = C.CFUNCTYPE(None)
        input_type = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
        self.callbacks = [env_type(self.environment), video_type(self.on_video),
                          audio_type(lambda l, r: None), batch_type(lambda d, n: n),
                          poll_type(lambda: None), input_type(self.input)]
        for name, callback in zip(("environment", "video_refresh", "audio_sample",
                                   "audio_sample_batch", "input_poll", "input_state"), self.callbacks):
            getattr(self.lib, "retro_set_" + name)(callback)
        self.lib.retro_init()
        self.lib.retro_load_game.argtypes = [C.POINTER(Game)]
        self.lib.retro_load_game.restype = C.c_bool
        self.rom_data = C.create_string_buffer(rom.read_bytes() if rom_bytes is None else rom_bytes)
        self.rom_path = str(rom.resolve()).encode()
        game = Game(self.rom_path, C.cast(self.rom_data, C.c_void_p), len(self.rom_data) - 1, None)
        if not self.lib.retro_load_game(C.byref(game)):
            raise RuntimeError("mGBA could not load ROM")

    def environment(self, command, data):
        cmd = command & 0xFFFF
        if cmd in (9, 30, 31):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self.directory
            return True
        if cmd == 10:
            self.pixel_format = C.cast(data, C.POINTER(C.c_int))[0]
            return self.pixel_format in (0, 1, 2)
        if cmd == 16:
            variables = C.cast(data, C.POINTER(Variable))
            i = 0
            while variables[i].key:
                variable = variables[i]
                self.options[variable.key] = variable.value.split(b"; ", 1)[-1].split(b"|")[0]
                i += 1
            return True
        if cmd == 15:
            variable = C.cast(data, C.POINTER(Variable)).contents
            if variable.key in self.options:
                variable.value = self.options[variable.key]
                return True
            return False
        if cmd == 17:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if cmd == 36:
            mapping = C.cast(data, C.POINTER(MemoryMap)).contents
            self.maps = [Descriptor.from_buffer_copy(mapping.descriptors[i]) for i in range(mapping.count)]
            return True
        if cmd == 52:
            C.cast(data, C.POINTER(C.c_uint))[0] = 0
            return True
        if cmd == 51:
            return True
        if cmd == 3:
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        if cmd in (11, 18, 35):
            return True
        return False

    def input(self, port, device, index, button):
        if port != 0 or device != 1:
            return 0
        return self.mask if button == 256 else int(bool(self.mask & (1 << button)))

    def on_video(self, data, width, height, pitch):
        if data and data != C.c_void_p(-1).value:
            self.video = (C.string_at(data, height * pitch), width, height, pitch, self.pixel_format)

    def read(self, address, size=1):
        for d in self.maps:
            if d.ptr and d.start <= address and address + size <= d.start + d.len:
                return C.string_at(d.ptr + d.offset + address - d.start, size)
        raise ValueError(f"No direct memory descriptor for {address:08x}; maps: " +
                         str([(hex(d.start), hex(d.len)) for d in self.maps]))

    def number(self, address, size=1):
        return int.from_bytes(self.read(address, size), "little")

    def state(self):
        result = {"frame": self.frames, "mode": self.number(0x030077D8),
                  "state": self.number(0x03003774), "category": self.number(0x0300378B)}
        if result["state"] == 0x4F:
            obj = self.number(0x030075E0, 4)
            if obj:
                result["substate"] = self.number(obj + 0x1E7)
        return result

    def run(self, frames, *buttons):
        self.mask = sum(1 << BUTTONS[b] for b in buttons)
        for _ in range(frames):
            self.lib.retro_run()
            self.frames += 1
            state = self.state()
            signature = tuple((k, v) for k, v in state.items() if k != "frame")
            if not self.trace or signature != self.trace[-1][0]:
                self.trace.append((signature, state))
        self.mask = 0

    def press(self, button, settle=90):
        self.run(2, button)
        self.run(settle)

    def until(self, predicate, limit=1800):
        for _ in range(limit):
            if predicate(self.state()):
                return
            self.run(1)
        raise AssertionError(f"Timed out waiting for game state: {self.state()}")

    def inventory(self):
        return self.read(0x03002C2E, 36)

    def savedata(self):
        self.lib.retro_get_memory_size.argtypes = [C.c_uint]
        self.lib.retro_get_memory_size.restype = C.c_size_t
        self.lib.retro_get_memory_data.argtypes = [C.c_uint]
        self.lib.retro_get_memory_data.restype = C.c_void_p
        size = self.lib.retro_get_memory_size(0)  # RETRO_MEMORY_SAVE_RAM.
        address = self.lib.retro_get_memory_data(0)
        assert address and size, "Core did not expose cartridge save memory"
        return C.string_at(address, size)

    def save_trace(self):
        (self.artifacts / "trace.json").write_text(json.dumps([s for _, s in self.trace], indent=2) + "\n")

    def screenshot(self, name):
        pixels, width, height, pitch, fmt = self.video
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            for x in range(width):
                if fmt == 1:
                    b, g, r, _ = pixels[y * pitch + x * 4:y * pitch + x * 4 + 4]
                else:
                    value = struct.unpack_from("<H", pixels, y * pitch + x * 2)[0]
                    if fmt == 2:
                        r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
                        g = g * 255 // 63
                    else:
                        r, g, b = (value >> 10) & 31, (value >> 5) & 31, value & 31
                        g = g * 255 // 31
                    r, b = r * 255 // 31, b * 255 // 31
                rows += bytes((r, g, b))
        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
        (self.artifacts / name).write_bytes(png)

    def close(self):
        self.lib.retro_unload_game()
        self.lib.retro_deinit()


def boot_to_file_select(e):
    """Cold boot with no save and navigate the existing title screens."""
    e.run(180)
    for _ in range(7):
        e.press("A", 180)
    e.run(300)
    for _ in range(2):
        e.press("A", 300)
    assert e.state()["state"] == 9


def boot_to_map(e, world_e=False):
    """Cold boot to the normal map or World-e using ordinary controller input."""
    boot_to_file_select(e)
    if world_e:
        for _ in range(3):e.press('Down',60)
        e.press('A',600)
    else:
        for _ in range(2):e.press('A',300)
    state=0x4C if world_e else 0x11
    assert e.state()['state']==state
    return state
