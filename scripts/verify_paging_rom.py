"""Input-only integration checks for the separate 18-row paging fixture.

Build it with: python scripts/build_test_rom.py --paging-test
It repeats the first two applicable cards per group; the normal output uses all BINs.
"""
import argparse
import json
from pathlib import Path

from build_test_rom import ROOT, sha256
from verify_test_rom import Emulator, boot_to_file_select
from verify_menu_rom import menu_state, confirmation_state, open_menu, confirm_selected, finish_power


MOTION = [1,2,2,2,3,3,3,3,3,3,3,2,2,1,1,1,0,0,0,0,0,1]


def animation_state(e):
    screen=e.number(0x030075E0,4)
    return list(e.read(screen+0x204,2))  # animation, loaded graphics bank.


def banner(e):
    """The native category sign's OBJ entries, independent of OAM slots."""
    import struct
    return [entry for entry in struct.iter_unpack('<4H',e.read(0x07000000,0x400))
            if not entry[0]&0x200 and entry[0]&255==1 and entry[2]&0xF000==0xA000]


def check_list(e,group,name):
    before=e.read(0x03002A60,0x31C)
    flash=e.savedata()
    native_banner=banner(e)
    assert len(native_banner)==(4 if group in (0,3) else 3), native_banner
    old_vram=e.read(0x06008000,0x4800)
    old_map=e.read(0x0600C800,0x800)
    open_menu(e,group)
    e.press('Up',10)
    assert menu_state(e)[1]==0
    assert banner(e)==native_banner, 'Native banner changed or disappeared'
    assert animation_state(e)[1]==0
    e.screenshot(name+'-page-1.png')
    # Check the actual generated VRAM patches over two whole native cycles.
    screen=e.number(0x030075E0,4)
    descriptor=0x08774000+group*12
    pages=e.number(descriptor+8,4)
    arrow_maps=e.number(pages+8,4)
    sx=(e.number(0x03003750,2)>>3)&31
    sy=(e.number(0x03003754,2)>>3)&31
    samples=[]
    for i in range(44):
        phase=e.number(screen+0x204)
        expected=e.read(arrow_maps+MOTION[phase]*12,12)
        actual=b''.join(e.read(0x0600C800+((((8+y+sy)&31)*32+(27+x+sx)%32)*2),2)
                        for y in range(2) for x in range(3))
        assert actual==expected, (phase,actual.hex(),expected.hex())
        samples.append(actual)
        if i<22:e.screenshot(f'{name}-arrow-{i:02}.png')
        e.run(1)
    assert samples[:22]==samples[22:] and len(set(samples))==4
    for _ in range(8):e.press('Down',10)
    assert menu_state(e)[1]==7, 'Down crossed the bottom of the first page'
    e.press('Right',10)
    for _ in range(7):e.press('Up',10)
    assert menu_state(e)[1]==8 and animation_state(e)[1]==1
    e.press('Up',10)
    assert menu_state(e)[1]==8, 'Up crossed the top of the second page'
    e.screenshot(name+'-page-2.png')
    # Right/Left preserve the row when it exists; clamp on a short final page.
    for key,index in (('Right',16),('Up',16),('Right',0),('Left',16),('Down',17),('Down',17),
                      ('Right',1),('Right',9),('Right',17),('Right',1),
                      ('Up',0),('Up',0),('Left',16),('Down',17)):
        e.press(key,10)
        assert menu_state(e)[1]==index, (key,menu_state(e))
        assert animation_state(e)[1]==index//8
    e.screenshot(name+'-page-3.png')
    # Held paging input, like held row input, moves just once.
    e.run(90,'Left');e.run(10)
    assert menu_state(e)[1]==9
    e.press('Left',10)
    assert menu_state(e)[1]==1
    e.press('Left',10)
    assert menu_state(e)[1]==17
    e.press('A',10)
    assert confirmation_state(e)==[1,0]
    assert animation_state(e)[1]==16+17
    # A confirmation has its own tile numbering and corresponding arrow maps.
    popup_arrows=e.number(pages+2*20+8,4)+(1+(17%8))*48
    samples=[]
    for _ in range(44):
        phase=e.number(screen+0x204)
        expected=e.read(popup_arrows+MOTION[phase]*12,12)
        actual=b''.join(e.read(0x0600C800+((((8+y+sy)&31)*32+(27+x+sx)%32)*2),2)
                        for y in range(2) for x in range(3))
        assert actual==expected, (phase,actual.hex(),expected.hex())
        samples.append(actual)
        e.run(1)
    assert samples[:22]==samples[22:] and len(set(samples))==4
    e.screenshot(name+'-last-page-confirm.png')
    e.press('B',10)
    assert menu_state(e)[1]==17 and confirmation_state(e)[0]==0
    assert animation_state(e)[1]==2
    e.press('A',10);e.press('Right',10);e.press('A',10)
    assert menu_state(e)[1]==17 and confirmation_state(e)[0]==0
    assert banner(e)==native_banner
    assert e.read(0x03002A60,0x31C)==before and e.savedata()==flash
    e.press('B',120)
    assert menu_state(e)[0]==0 and e.state()['substate']==2
    assert e.read(0x06008000,0x4800)==old_vram
    assert e.read(0x0600C800,0x800)==old_map
    open_menu(e,group,hold=False)
    assert menu_state(e)[1]==0 and animation_state(e)[1]==0
    e.press('Left',10);e.press('Down',10)
    assert menu_state(e)[1]==17
    expected_id=e.number(e.number(descriptor,4)+17*8,4)
    expected_id=e.number(expected_id,2)&511
    confirm_selected(e)
    assert e.number(0x030039FA,2)==expected_id, 'Wrong card activated from final page'
    back_state=0x11 if e.number(0x03003DCF)&0x40 else 0x4C
    if group in (0,3):finish_power(e,back_state)
    else:
        e.until(lambda state:state['state']==0x1A)
        e.until(lambda state:state['state']==back_state,limit=30000)
    return {'group':group,'pages':3,'rows':18,'arrow_period':22,
            'arrow_positions':4,'native_banner_preserved':True,
            'confirmation_arrow_bank_verified':True,
            'vertical_page_edges_stop':True,
            'last_page_activated_id':expected_id,'passed':True}


def run_context(core,rom,artifacts,world_e):
    e=Emulator(core,rom,artifacts)
    try:
        boot_to_file_select(e)
        if world_e:
            for _ in range(3):e.press('Down',60)
            e.press('A',600)
        else:
            for _ in range(2):e.press('A',300)
        result=[]
        for powerup,group in ((True,3 if world_e else 0),(False,2 if world_e else 1)):
            e.press('R')
            # Normal R popup remembers Power-Up; World-e always resets to Demo.
            if powerup or not world_e:e.press('Down')
            e.press('A',600)
            result.append(check_list(e,group,'powerup' if powerup else 'demo'))
        return result
    finally:e.save_trace();e.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,default=Path('C:/RetroBat/emulators/retroarch/cores/mgba_libretro.dll'))
    p.add_argument('--rom',type=Path,default=ROOT/'output/sma4-card-menu-paging-test.gba')
    p.add_argument('--artifacts',type=Path,default=ROOT/'output/verification-paging')
    args=p.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    result_path=args.artifacts/'result.json'
    result_path.unlink(missing_ok=True)
    manifest=json.loads(args.rom.with_suffix('.json').read_text())
    assert all(len(g['cards'])==18 for g in manifest['groups']), 'Requires the --paging-test fixture'
    assert sha256(args.rom.read_bytes())==manifest['output_sha256']
    report={'passed':True,'rom_sha256':manifest['output_sha256'],
            'normal':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts/'normal',False),
            'world_e':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts/'world-e',True),
            'memory_writes_by_test':0}
    result_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
