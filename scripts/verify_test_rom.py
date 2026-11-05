"""Headless integration check using an installed mGBA libretro core (ctypes only).

The emulator receives ordinary controller input. No game RAM is modified.
ABI reference: https://github.com/libretro/libretro-common/blob/master/include/libretro.h
"""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import struct
import zlib

from build_rom import (ITEMS, ROM_NAME, DEMO_NAME, WORLD_E_DEMO_NAME,
                            PAYLOAD, sha256, ROM_SHA256)

ROOT = Path(__file__).resolve().parents[1]
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


def verify_world_e(core, rom, artifacts):
    """Enter World-e through Level Card; watch its exclusive Demo twice."""
    e = Emulator(core, rom, artifacts)
    try:
        boot_to_file_select(e)
        for _ in range(3):
            e.press("Down", 60)
        e.screenshot("01-level-card-option.png")
        e.press("A", 600)
        assert e.state()["state"] == 0x4C and e.number(0x03003A2F) == 1
        assert e.number(0x03003DCF) & 0x40 == 0
        e.screenshot("02-world-e-map.png")
        saved_progress = e.read(0x03002A60, 0x31C)
        saved_flash = e.savedata()
        card = (ROOT / "eReader_cards" / "Demo (World-e)" / WORLD_E_DEMO_NAME).read_bytes()
        replay = card[PAYLOAD + 12:].ljust(0x800, b"\0")

        def scan_screen():
            e.press("R")
            e.press("A", 600)
            assert e.state().get("substate") == 2
            assert e.number(0x03003DCF) & 0x40 == 0, "World-e context lost on communication screen"

        scan_screen()
        e.screenshot("03-demo-ready.png")
        e.press("B", 600)
        assert e.state()["state"] == 0x4C
        assert e.read(0x03002A60, 0x31C) == saved_progress
        assert e.savedata() == saved_flash
        runs = []
        for attempt in (1, 2):
            scan_screen()
            e.press("A", 10)
            assert e.number(0x030039FA, 2) == 102, "World-e must select Bombarded, never the fortress Demo"
            assert e.number(0x030039AD) == 4 and e.number(0x030039F6) == 2
            assert e.read(0x020143D0, 0x800) == replay
            assert e.read(0x02013BD0, 0x800) == replay
            e.until(lambda s: s["state"] == 0x1A)
            start = e.frames
            positions = []
            seen_levels = set()
            while e.state()["state"] != 0x4C and e.frames - start < 15000:
                elapsed = e.frames - start
                if e.state()["state"] == 0x1A:
                    assert e.number(0x03003A2F) == 1
                    seen_levels.add(e.number(0x03003B08, 2))
                    player = e.number(0x03007818, 4)
                    positions.append([elapsed, e.number(player, 4) // 256,
                                      e.number(player + 4, 4) // 256, e.number(0x03002C58)])
                if attempt == 1 and elapsed in (120, 4800, 9600):
                    e.screenshot(f"04-demo-playback-{elapsed:05d}.png")
                e.run(30)  # No controller input after confirmation.
            assert e.state()["state"] == 0x4C, "World-e Demo did not finish"
            assert seen_levels == {0, 1, 2}, seen_levels
            e.run(90)
            assert e.read(0x03002A60, 0x31C) == saved_progress
            assert e.savedata() == saved_flash, "World-e Demo changed cartridge save memory"
            runs.append({"attempt": attempt, "playback_frames": e.frames - start - 90,
                         "sublevels": sorted(seen_levels), "positions": positions})
        assert runs[0]["positions"] == runs[1]["positions"], "World-e repeat playback diverged"
        e.screenshot("05-returned-to-world-e.png")
        (artifacts / "demo-runs.json").write_text(json.dumps(runs, indent=2) + "\n")
        return {"passed": True, "frames": e.frames, "card_id": 102, "applicability": 2,
                "playback_frames": [r["playback_frames"] for r in runs],
                "checks": ["Level Card entry from file select", "World-e context on communication screen",
                           "cancel preserves progress", "exclusive World-e card ID and payload",
                           "two complete input-free playbacks", "three Bombarded sublevels",
                           "deterministic repeat playback", "return to World-e",
                           "save-file RAM and cartridge save memory unchanged"]}
    finally:
        e.save_trace()
        e.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core", type=Path, default=Path("C:/RetroBat/emulators/retroarch/cores/mgba_libretro.dll"))
    p.add_argument("--rom", type=Path)
    p.add_argument("--artifacts", type=Path)
    p.add_argument("--power-up-only", action="store_true", help="Verify the earlier item-only experiment")
    args = p.parse_args()
    args.rom = args.rom or ROOT / "output" / ("sma4-10-item-test.gba" if args.power_up_only else "sma4-card-tests.gba")
    args.artifacts = args.artifacts or ROOT / "output" / ("verification" if args.power_up_only else "verification-card-tests")
    original = ROOT / "original_roms" / ROM_NAME
    assert sha256(original.read_bytes()) == ROM_SHA256, "Unexpected original ROM"
    args.artifacts.mkdir(parents=True, exist_ok=True)
    (args.artifacts / "result.json").unlink(missing_ok=True)
    e = Emulator(args.core.resolve(), args.rom.resolve(), args.artifacts.resolve())
    try:
        boot_to_file_select(e)
        for _ in range(2):
            e.press("A", 300)
        assert e.state()["state"] == 0x11, e.state()
        assert e.inventory() == bytes(36)
        e.screenshot("01-world-map.png")

        selected_powerup = False

        def scan_screen(powerup=True):
            nonlocal selected_powerup
            e.press("R")
            if powerup != selected_powerup:
                e.press("Down")
                selected_powerup = powerup
            e.press("A", 600)
            assert e.state().get("substate") == 2, e.state()

        # Cancel at the exact communication screen supplied by the user.
        scan_screen()
        e.screenshot("02-ready-to-scan.png")
        e.press("B", 600)
        assert e.state()["state"] == 0x11, e.state()
        assert e.inventory() == bytes(36), "Cancel must not grant items"

        scan_screen()
        e.press("A", 300)
        assert e.number(0x030039FA, 2) == 31
        assert e.number(0x030039AD) == 3  # Native card category.
        assert e.inventory() == ITEMS + bytes(26), e.inventory().hex()
        e.screenshot("03-ten-items-granted.png")
        # Holding A does not re-enter the loader on every frame.
        e.run(120, "A")
        assert e.inventory() == ITEMS + bytes(26)
        e.until(lambda s: s.get("substate") == 32)
        e.run(60)
        e.screenshot("04-continue-prompt.png")

        # Accept repeat scans. Native overflow handling keeps the most recent 36 items.
        for count in (2, 3, 4):
            e.press("A", 300)
            expected = (ITEMS * count)[-36:]
            assert e.inventory() == expected.ljust(36, b"\0"), e.inventory().hex()
            e.press("A", 300)
            assert e.state().get("substate") == 32, e.state()
        e.screenshot("05-full-inventory.png")
        full_inventory = e.inventory()
        e.press("Right")
        e.press("A", 600)
        assert e.state()["state"] == 0x11, e.state()
        assert e.inventory() == full_inventory
        e.press("B", 120)
        e.screenshot("06-returned-to-map.png")
        e.press("B", 120)

        demo_runs = []
        if args.power_up_only:
            # Historical item-only build retains the original Demo link behavior.
            scan_screen(powerup=False)
            e.press("A", 15)
            assert e.state().get("substate") == 4, e.state()
            assert e.inventory() == full_inventory
            e.screenshot("07-demo-original-link-path.png")
        else:
            saved_progress = e.read(0x03002A60, 0x31C)
            replay = (ROOT / "eReader_cards" / "Demo" / DEMO_NAME).read_bytes()[PAYLOAD + 12:].ljust(0x800, b"\0")
            scan_screen(powerup=False)
            e.screenshot("07-demo-ready.png")
            e.press("B", 600)
            assert e.state()["state"] == 0x11
            assert e.read(0x03002A60, 0x31C) == saved_progress, "Cancelling Demo changed progress"

            # Watch twice; after the confirm there is no controller input at all.
            for attempt in (1, 2):
                scan_screen(powerup=False)
                assert e.number(0x03003DCF) & 0x40, "Normal game context missing"
                e.press("A", 10)
                assert e.number(0x030039FA, 2) == 16
                assert e.number(0x030039AD) == 4 and e.number(0x030039F6) == 1
                assert e.read(0x020143D0, 0x800) == replay, "Replay staging differs from BIN"
                assert e.read(0x02013BD0, 0x800) == replay, "Native parser backup differs from BIN"
                e.until(lambda s: s["state"] == 0x1A)
                start_frame = e.frames
                seen_levels = set()
                positions = []
                while e.state()["state"] != 0x11 and e.frames - start_frame < 6000:
                    if e.state()["state"] == 0x1A:
                        seen_levels.add(e.number(0x03003B08, 2))
                        player = e.number(0x03007818, 4)
                        positions.append([e.frames - start_frame, e.number(player, 4) // 256,
                                          e.number(player + 4, 4) // 256])
                    elapsed = e.frames - start_frame
                    if attempt == 1 and elapsed in (120, 1200, 2520):
                        e.screenshot(f"08-demo-playback-{elapsed:04d}.png")
                    e.run(30)
                assert e.state()["state"] == 0x11, "Demo failed to finish and return to map"
                assert seen_levels == {0x1E1, 0x1E2}, seen_levels
                assert max(p[1] for p in positions) > 3700, "Replay did not reach the boss room"
                e.run(90)
                assert e.read(0x03002A60, 0x31C) == saved_progress, "Demo changed saved progress/inventory"
                demo_runs.append({"attempt": attempt, "playback_frames": e.frames - start_frame - 90,
                                  "sublevels": sorted(seen_levels), "positions": positions})
            assert demo_runs[0]["positions"] == demo_runs[1]["positions"], "Repeat playback diverged"
            e.screenshot("09-demo-returned-to-map.png")
            # Switching back after watching the demo must still load the item card.
            scan_screen(powerup=True)
            e.press("A", 300)
            assert e.number(0x030039FA, 2) == 31 and e.number(0x030039AD) == 3
            assert e.inventory() == (full_inventory + ITEMS)[-36:]
            e.screenshot("10-power-up-after-demo.png")
            (e.artifacts / "demo-runs.json").write_text(json.dumps(demo_runs, indent=2) + "\n")
        assert sha256(original.read_bytes()) == ROM_SHA256
        report = {"passed": True, "core": str(args.core.resolve()),
                  "core_sha256": sha256(args.core.read_bytes()),
                  "patched_rom_sha256": sha256(args.rom.read_bytes()),
                  "frames": e.frames, "initial_grant": list(ITEMS),
                  "inventory_after_four_scans": list(full_inventory),
                  "final_inventory": list(e.inventory()),
                  "checks": ["cold boot to world map", "cancel grants nothing", "native parser card ID/type",
                             "all ten items granted", "held A does not duplicate grant",
                             "repeat scans", "native 36-item overflow", "return to map",
                             *( ["Demo uses original link path"] if args.power_up_only else
                                ["cancel Demo preserves progress", "Demo ID/type and replay buffers",
                                 "normal game selects only fortress Demo", "two complete Demo playbacks without input",
                                 "World 8 fortress front/back sublevels",
                                 "repeat playback is deterministic", "Demo restores entire save-file RAM",
                                 "Power-Up after Demo"] ),
                             "original ROM hash unchanged"],
                  "memory_writes_by_test": 0}
    finally:
        e.save_trace()
        e.close()
    if not args.power_up_only:
        report["world_e"] = verify_world_e(args.core.resolve(), args.rom.resolve(),
                                           args.artifacts.resolve() / "world-e")
    assert sha256(original.read_bytes()) == ROM_SHA256
    (args.artifacts / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
