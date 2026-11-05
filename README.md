# Super Mario Advance 4: e-Deluxe

_e-Deluxe_ is a hack of Super Mario Advance 4 that builds all officially-released e-Reader cards into the game.

Opening the Power-Up Card or Demo Card screen will lead to a menu where you can select any card to activate immediately without needing an e-Reader or second GBA. Cards whose effects aren't self-explanatory, such as the switches, also have in-game descriptions.

And since this hack is built off the Virtual Console release, all official level cards are already built-in, but the ability to scan additional level cards still exists.

## Building

With Python 3 and Clang installed, run from the repository root:

```powershell
python -B scripts/build_rom.py
```

This creates `output/Super Mario Advance 4 - e-Deluxe.gba` using the ROM in `original_roms` and cards in `eReader_cards`.

## Known Compatible Patches

Based on my testing, the following patches are also compatible with this hack (either individually or all together):
- [Bug fixes](https://www.romhacking.net/hacks/6153/) by Mario_GMD
- [GBP/SNES Palette Hack](https://www.romhacking.net/hacks/6227/) by djedditt
- [Cartridge Rumble](https://www.romhacking.net/hacks/6234/) by djedditt
- [Voice Removal](https://www.romhacking.net/hacks/2566/) by mangaman3000

Others may also be compatible, but I didn't test them.

## Notes
- This patch is designed for the `(USA) (Rev 1) (Virtual Console)` ROM. The non-Virtual Console variant and other regions are untested.
- AI was used in the development of this hack (specifically, GPT 6.1 Sol), with human playtesting by me. Everything works as far as I can tell, but feel free to submit an issue if you find any bugs and I will fix them.

## Special Thanks
- KarisaAdvynia: For the [SMA4 disassembly](https://github.com/KarisaAdvynia/sma4-disasm)
- Renegade_X135: For [converting](https://gbatemp.net/threads/super-mario-advance-4-all-unreleased-e-reader-cards-translated.529179/) SMA4's Japanese e-Reader cards to support the US version
