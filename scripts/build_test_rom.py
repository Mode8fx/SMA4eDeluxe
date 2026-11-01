"""Build fixed Power-Up and Demo Card experiments. Python 3; no assembler required."""

import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
ROM_NAME = "Super Mario Advance 4 - Super Mario Bros. 3 (USA) (Rev 1) (Virtual Console).gba"
CARD_NAME = "[Japan] [25] 10-Item Set.bin"
DEMO_NAME = "[Japan] [16] World 8-Fortress Speed Stage.bin"
DEMO_SHA256 = "34d4257ed813cba5acc5d6dae82585e2c3c3219221c3819b7d9739215697cd26"
WORLD_E_DEMO_NAME = "[Japan] [102] World-e Bombarded by Bob-ombs All Coins.bin"
WORLD_E_DEMO_SHA256 = "f513f2cf16c11da26fe93b739cf11278f5fecbddaa85760717a252ba13cacd18"
ROM_SHA256 = "c8db146071462572b3be6d2caefe453c584547515f9a2e3e75264cdf9243442e"
BASE = 0x08000000
HOOK = 0x23EE90  # Communication substate 3: originally Sub080C1038+1.
CODE = 0x7F0000
CARD = 0x7F0200
DEMO = 0x7F1000
DEMO_TRANSFER = 0x7F2000  # Aligned game payload and zero padding for native copies.
WORLD_E_DEMO = 0x7F3000
WORLD_E_DEMO_TRANSFER = 0x7F4000
PAYLOAD = 0x72  # This BIN's e-Reader application envelope precedes game data.
ITEMS = bytes.fromhex("01 02 03 09 04 05 06 08 0e 0f")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Thumb:
    """Small ARM7TDMI Thumb emitter with checked PC-relative fixups and a listing."""

    def __init__(self, base):
        self.base = base
        self.data = bytearray()
        self.labels = {}
        self.fixups = []
        self.literals = {}
        self.listing = []

    def emit(self, word, description):
        self.listing.append(f"{self.base + len(self.data):08X}: {word:04X}  {description}")
        self.data += struct.pack("<H", word)

    def label(self, name):
        self.labels[name] = len(self.data)

    def literal(self, reg, value):
        name = f"literal_{value:08x}"
        self.literals[name] = value
        self.fixups.append((len(self.data), "ldr", name, reg))
        self.emit(0x4800 | reg << 8, f"ldr r{reg}, =0x{value:08X}")

    def branch(self, label, condition=None):
        self.fixups.append((len(self.data), "b", label, condition))
        self.emit(0xE000 if condition is None else 0xD000 | condition << 8, f"b{condition if condition is not None else ''} {label}")

    def call(self, address):
        self.literal(7, address | 1)
        self.fixups.append((len(self.data), "bl", "call_r7", None))
        self.emit(0xF000, "bl call_r7 (high)")
        self.emit(0xF800, "bl call_r7 (low)")

    def finish(self):
        self.label("call_r7")
        self.emit(0x4738, "bx r7")
        while len(self.data) % 4:
            self.emit(0x46C0, "nop / align literal pool")
        for name, value in self.literals.items():
            self.label(name)
            self.data += struct.pack("<I", value)
        for offset, kind, label, arg in self.fixups:
            target = self.base + self.labels[label]
            pc = self.base + offset + 4
            if kind == "ldr":
                delta = target - (pc & ~3)
                assert delta % 4 == 0 and 0 <= delta <= 1020
                struct.pack_into("<H", self.data, offset, 0x4800 | arg << 8 | delta // 4)
            elif kind == "bl":
                delta = target - pc
                assert delta % 2 == 0 and -(1 << 22) <= delta < (1 << 22)
                struct.pack_into("<HH", self.data, offset, 0xF000 | ((delta >> 12) & 0x7FF), 0xF800 | ((delta >> 1) & 0x7FF))
            else:
                delta = (target - pc) // 2
                bits = 11 if arg is None else 8
                assert -(1 << (bits - 1)) <= delta < (1 << (bits - 1))
                opcode = 0xE000 if arg is None else 0xD000 | arg << 8
                struct.pack_into("<H", self.data, offset, opcode | (delta & ((1 << bits) - 1)))
        # Replace placeholder opcodes with the resolved instruction words.
        self.listing = [line[:10] + f"{struct.unpack_from('<H', self.data, int(line[:8], 16) - self.base)[0]:04X}" + line[14:]
                        for line in self.listing]
        return bytes(self.data)


def make_loader(sound_address, include_demo=False, include_world_e_demo=False):
    t = Thumb(BASE + CODE)
    t.emit(0xB5F0, "push {r4-r7, lr}")
    t.emit(0xB081, "sub sp, #4 / window message argument")
    t.emit(0x1C04, "mov r4, r0 / communication screen object")
    t.literal(1, 0x0300378B)
    t.emit(0x7809, "ldrb r1, [r1] / requested card category")
    t.emit(0x22F0, "movs r2, #0xF0")
    t.emit(0x4011, "ands r1, r2")
    t.emit(0x2920, "cmp r1, #0x20 / Power-Up Card")
    if include_demo:
        t.branch("supported", 0)
        t.emit(0x2910, "cmp r1, #0x10 / Demo Card")
        t.branch("fallback", 1)
        t.label("supported")
        t.emit(0x1C0D, "mov r5, r1 / preserve requested category")
    else:
        t.branch("fallback", 1)
    t.emit(0x6860, "ldr r0, [r4, #4] / upper dialogue")
    t.emit(0x7A00, "ldrb r0, [r0, #8] / window animation state")
    t.emit(0x2800, "cmp r0, #0")
    t.branch("return", 1)
    if include_demo:
        t.emit(0x2D10, "cmp r5, #0x10")
        t.branch("powerup", 1)
        # Use the same persistent context bit as native card applicability checks.
        # This remains valid while the communication screen is open.
        t.literal(1, 0x03003DCF)
        t.emit(0x7809, "ldrb r1, [r1] / communication context flags")
        t.emit(0x2240, "movs r2, #0x40 / main game bit")
        t.emit(0x4011, "ands r1, r2")
        t.emit(0x2900, "cmp r1, #0")
        t.branch("world_e_demo" if include_world_e_demo else "fallback", 0)
        t.literal(6, BASE + DEMO_TRANSFER)
        if include_world_e_demo:
            t.branch("copy_demo")
            t.label("world_e_demo")
            t.literal(6, BASE + WORLD_E_DEMO_TRANSFER)
        t.label("copy_demo")
        # Native Demo code accesses these fixed buffers independently of r0.
        for offset, destination in ((0, 0x02004000), (12, 0x020143D0)):
            t.emit(0x1C30, "mov r0, r6 / context-selected Demo transfer record")
            if offset:
                t.emit(0x300C, "adds r0, #12 / skip game and Demo headers")
            t.literal(1, destination)
            t.literal(2, 0x200)  # CpuFastSet count is words: 0x800 bytes.
            t.call(0x080E7590)
        t.literal(0, 0x02004000)
        t.branch("parse")
        t.label("powerup")
    t.literal(0, BASE + CARD + PAYLOAD)
    t.label("parse")
    t.call(0x080E3098)  # Native dispatcher -> item or demo parser.
    t.literal(1, 0x0300376C)
    t.emit(0x200C, "movs r0, #12 / same flag as successful link poll")
    t.emit(0x7008, "strb r0, [r1]")
    t.literal(1, 0x030079D8)
    t.emit(0x2002, "movs r0, #2 / successful receive status")
    t.emit(0x6008, "str r0, [r1]")
    t.literal(1, 0x03002D7C)
    t.emit(0x2006, "movs r0, #6 / success sound")
    t.call(sound_address)
    t.emit(0x6820, "ldr r0, [r4] / main dialogue")
    t.emit(0x2104, "movs r1, #4 / received message")
    t.emit(0x9100, "str r1, [sp]")
    t.emit(0x2178, "movs r1, #120")
    t.emit(0x2250, "movs r2, #80")
    t.emit(0x2300, "movs r3, #0")
    t.call(0x080C4BC0)
    t.emit(0x6821, "ldr r1, [r4]")
    t.emit(0x200B, "movs r0, #11 / native success window mode")
    t.emit(0x7288, "strb r0, [r1, #10]")
    t.literal(1, 0x1CE)
    t.emit(0x1861, "adds r1, r4, r1")
    t.emit(0x2000, "movs r0, #0")
    t.emit(0x8008, "strh r0, [r1] / clear timer")
    t.literal(1, 0x1E7)
    t.emit(0x1861, "adds r1, r4, r1")
    t.emit(0x2006, "movs r0, #6 / native validation and reward sequence")
    t.emit(0x7008, "strb r0, [r1]")
    t.branch("return")
    t.label("fallback")
    t.emit(0x1C20, "mov r0, r4")
    t.call(0x080C1038)  # Keep unhandled categories on their original path.
    t.label("return")
    t.emit(0xB001, "add sp, #4")
    t.emit(0xBCF0, "pop {r4-r7}")
    t.emit(0xBC01, "pop {r0}")
    t.emit(0x4700, "bx r0")
    code = t.finish()
    return code, t.listing


def build(rom_path, card_path, output, demo_path=None, world_e_demo_path=None):
    original = rom_path.read_bytes()
    card = card_path.read_bytes()
    demo = demo_path.read_bytes() if demo_path else None
    world_e_demo = world_e_demo_path.read_bytes() if world_e_demo_path else None
    if sha256(original) != ROM_SHA256:
        raise ValueError("Expected the unmodified USA Rev 1 Virtual Console ROM (see Notes.md).")
    if len(card) != 0x840 or card[PAYLOAD:PAYLOAD + 22] != bytes.fromhex("1f 06 00 00 00 00 00 00 00 0a 00 00") + ITEMS:
        raise ValueError("This experiment requires the supplied region-converted 10-Item Set BIN.")
    if demo is not None and sha256(demo) != DEMO_SHA256:
        raise ValueError("This experiment requires the supplied World 8-Fortress Speed Stage BIN.")
    if world_e_demo is not None and (demo is None or sha256(world_e_demo) != WORLD_E_DEMO_SHA256):
        raise ValueError("World-e Demo requires both the main-game Demo and the supplied Bombarded by Bob-ombs BIN.")
    if original[HOOK:HOOK + 4] != struct.pack("<I", 0x080C1039):
        raise ValueError("Unexpected communication dispatch table.")
    code, listing = make_loader(0x080E4860, include_demo=demo is not None,
                               include_world_e_demo=world_e_demo is not None)
    transfer = demo[PAYLOAD:].ljust(0x80C, b"\0") if demo is not None else None
    world_e_transfer = world_e_demo[PAYLOAD:].ljust(0x80C, b"\0") if world_e_demo is not None else None
    end = DEMO_TRANSFER + len(transfer) if transfer is not None else CARD + len(card)
    if world_e_transfer is not None:
        end = WORLD_E_DEMO_TRANSFER + len(world_e_transfer)
    if len(code) > CARD - CODE or any(original[CODE:end]):
        raise ValueError("Injection area is not empty or loader overlaps the BIN.")
    if output in (rom_path, card_path, demo_path, world_e_demo_path) or ROOT / "original_roms" in output.parents:
        raise ValueError("Output must not overwrite an input or anything in original_roms.")
    patched = bytearray(original)
    patched[HOOK:HOOK + 4] = struct.pack("<I", BASE + CODE + 1)
    patched[CODE:CODE + len(code)] = code
    patched[CARD:CARD + len(card)] = card
    if demo is not None:
        patched[DEMO:DEMO + len(demo)] = demo
        patched[DEMO_TRANSFER:DEMO_TRANSFER + len(transfer)] = transfer
    if world_e_demo is not None:
        patched[WORLD_E_DEMO:WORLD_E_DEMO + len(world_e_demo)] = world_e_demo
        patched[WORLD_E_DEMO_TRANSFER:WORLD_E_DEMO_TRANSFER + len(world_e_transfer)] = world_e_transfer
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(patched)
    report = {
        "input_rom": str(rom_path), "input_sha256": sha256(original),
        "card": str(card_path), "card_sha256": sha256(card),
        "output_rom": str(output), "output_sha256": sha256(patched),
        "rom_size": len(patched), "hook_rom_offset": hex(HOOK),
        "code_rom_offset": hex(CODE), "code_size": len(code),
        "bin_rom_offset": hex(CARD), "payload_bin_offset": hex(PAYLOAD),
        "card_id": 31, "card_type": 3, "item_ids": list(ITEMS),
    }
    if demo is not None:
        report["demo"] = {"card": str(demo_path), "sha256": sha256(demo),
                          "bin_rom_offset": hex(DEMO), "transfer_rom_offset": hex(DEMO_TRANSFER),
                          "transfer_size": len(transfer), "card_id": 16, "card_type": 4}
        report["demo"]["context"] = "normal game only"
    if world_e_demo is not None:
        report["world_e_demo"] = {"card": str(world_e_demo_path), "sha256": sha256(world_e_demo),
                                  "bin_rom_offset": hex(WORLD_E_DEMO),
                                  "transfer_rom_offset": hex(WORLD_E_DEMO_TRANSFER),
                                  "transfer_size": len(world_e_transfer), "card_id": 102,
                                  "card_type": 4, "context": "World-e only"}
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    output.with_suffix(".loader.txt").write_text("\n".join(listing) + "\n")
    assert rom_path.read_bytes() == original
    print(json.dumps(report, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rom", type=Path, default=ROOT / "original_roms" / ROM_NAME)
    p.add_argument("--card", type=Path, default=ROOT / "eReader_cards" / "Power-Up" / CARD_NAME)
    p.add_argument("--demo", type=Path, default=ROOT / "eReader_cards" / "Demo" / DEMO_NAME)
    p.add_argument("--world-e-demo", type=Path, default=ROOT / "eReader_cards" / "Demo (World-e)" / WORLD_E_DEMO_NAME)
    p.add_argument("--power-up-only", action="store_true", help="Reproduce the earlier item-only experiment")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    output = args.output or ROOT / "output" / ("sma4-10-item-test.gba" if args.power_up_only else "sma4-card-tests.gba")
    build(args.rom.resolve(), args.card.resolve(), output.resolve(),
          None if args.power_up_only else args.demo.resolve(),
          None if args.power_up_only else args.world_e_demo.resolve())


if __name__ == "__main__":
    main()
