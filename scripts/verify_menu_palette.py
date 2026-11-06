"""Compare custom menus with native Level Card colours in GBA and GBP modes."""
import argparse
import json
import struct
from pathlib import Path

from build_rom import DEFAULT_OUTPUT, ROOT,ROM_NAME,sha256,GROUP_CATALOG
from menu_style import COLORS,native_palette
from ips import read_ips,apply_ips
from emulator import DEFAULT_CORE,Emulator,boot_to_map
from verify_menu_rom import (open_menu,highlight,parity,snapshot,restore,
                             assert_confirmation_description)


def adjusted_palette(original,mode):
    colors=native_palette(original)
    return [sum(struct.unpack_from('<H',original,0x121E3C+
                                  (0x100 if 4<=i<=8 else 0)+mode*32+
                                  ((color>>shift)&31)*2)[0]<<shift
                for shift in (0,5,10)) for i,color in enumerate(colors)]


def run_context(core,rom,image,artifacts,mode,world_e):
    e=Emulator(core,rom,artifacts,rom_bytes=image)
    try:
        original=(ROOT/'original_rom'/ROM_NAME).read_bytes()
        expected=adjusted_palette(original,mode)
        if mode==2:assert expected==COLORS, 'Normal GBA appearance changed'
        boot_to_map(e,world_e)
        if world_e:
            # Compare against the actual native menu, independent of our renderer.
            frame=struct.unpack('<16H',e.read(0x05000180,32))
            arrow=struct.unpack('<16H',e.read(0x050003A0,32))
            for i in (1,2,3,13,14,15):assert expected[i]==frame[i], (mode,i,expected[i],frame[i])
            for i in range(4,9):assert expected[i]==arrow[i-1], (mode,i,expected[i],arrow[i-1])
        assert e.number(0x0300378B)&6==mode
        baseline=snapshot(e)
        for group in ((3,2) if world_e else (0,1)):
            restore(e,baseline)
            e.press('R')
            if group in (0,3):e.press('Down')
            e.press('A',600)
            before=e.read(0x050001C0,32)
            open_menu(e,group,hold=False)
            actual=list(struct.unpack('<16H',e.read(0x050001C0,32)))
            assert actual==expected, (mode,group,actual,expected)
            name='powerup' if group in (0,3) else 'demo'
            e.screenshot(name+'-list.png')
            if group in (0,3):highlight(e,20)
            e.press('A',10)
            assert e.read(0x050001C0,32)==struct.pack('<16H',*expected)
            e.screenshot(name+'-confirmation.png')
            if group in (0,3):
                assert_confirmation_description(e,GROUP_CATALOG[group]['cards'][20])
            e.press('B',10);e.press('B',120)
            assert e.read(0x050001C0,32)==before
            parity(e,group,name)
        return {'passed':True,'palette_mode':mode,'palette':[hex(c) for c in expected],
                'native_level_card_colors_matched':world_e,
                'all_rows_confirmations_and_palette_restore_checked':True}
    finally:e.save_trace();e.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,default=DEFAULT_CORE)
    p.add_argument('--rom',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--artifacts',type=Path,default=ROOT/'output/verification-menu-palette')
    args=p.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    result=args.artifacts/'result.json'
    result.unlink(missing_ok=True)
    image=args.rom.read_bytes()
    patch=ROOT/'community_patches/GBP SNES Palette Hack [djedditt] (v1.0).ips'
    patched=apply_ips(image,read_ips(patch))
    report={'passed':True,'rom_sha256':sha256(image),'gbp_patch_sha256':sha256(patch.read_bytes()),
            'memory_writes_by_test':0,'gbp_fixture_created_in_memory':True}
    for mode,fixture,name in ((2,image,'gba'),(0,patched,'gbp')):
        for world_e in (False,True):
            context='world-e' if world_e else 'normal'
            report[name+'-'+context]=run_context(args.core.resolve(),args.rom.resolve(),fixture,
                                                args.artifacts/name/context,mode,world_e)
    assert report['gba-normal']['palette']!=report['gbp-normal']['palette']
    assert args.rom.read_bytes()==image
    result.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
