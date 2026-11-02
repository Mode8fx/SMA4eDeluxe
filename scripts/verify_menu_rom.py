"""Input-driven mGBA integration tests for all three shared menus and six cards."""
import argparse
import json
from pathlib import Path

from build_menu_rom import GROUP_CATALOG, decoded_card
from build_test_rom import ROOT, ROM_NAME, ROM_SHA256, PAYLOAD, ITEMS, sha256
from verify_test_rom import Emulator, boot_to_file_select


def menu_state(e):
    screen=e.number(0x030075E0,4)
    return list(e.read(screen+0x1F4,4))  # active, selected, armed, group.


def confirmation_state(e):
    screen=e.number(0x030075E0,4)
    return list(e.read(screen+0x1F4+12,2))  # confirming, Yes=0 / No=1.


def open_menu(e, group, hold=True):
    if hold:
        e.run(180,'A')  # Initial OK held down must never choose the first card.
        assert menu_state(e)[:3]==[1,0,0], menu_state(e)
        e.run(30)
    else:
        e.press('A',120)
    assert e.state().get('substate')==3 and menu_state(e)==[1,0,1,group]


def parity(e, group, name):
    """Run exactly the same behavior contract against each applicable list."""
    progress=e.read(0x03002A60,0x31C)
    flash=e.savedata()
    vram=[e.read(a,n) for a,n in ((0x06008000,0x2000),(0x0600C800,0x800),(0x050001C0,32))]
    native_tiles=e.read(0x06000000,0x8000)
    native_hud_palette=e.read(0x050001E0,32)
    previous_id=e.number(0x030039FA,2)
    open_menu(e,group)
    e.screenshot(name+'-first.png')
    e.run(90)
    assert e.number(0x030039FA,2)==previous_id
    e.run(60,'Down');e.run(30)
    assert menu_state(e)[1]==1, 'Holding Down must move once'
    e.screenshot(name+'-second.png')
    for key,expected in (('Down',0),('Up',1),('Up',0)):
        e.press(key,30)
        assert menu_state(e)[1]==expected
    for key in ('Left','Right','Start'):
        e.press(key,30)
        assert menu_state(e)[1]==0 and menu_state(e)[0]==1
    assert e.read(0x06000000,0x8000)==native_tiles, 'Selector overwrote native background/HUD graphics'
    assert e.read(0x050001E0,32)==native_hud_palette, 'Selector overwrote native HUD palette'
    e.press('Down',30)
    # Holding the selection press opens the prompt but cannot answer it.
    e.run(180,'A')
    assert menu_state(e)==[1,1,0,group] and confirmation_state(e)==[1,0]
    e.run(30)
    e.screenshot(name+'-confirm-yes.png')
    e.press('Down',30)
    assert menu_state(e)[1]==1 and confirmation_state(e)==[1,0]
    e.press('Right',30)
    assert confirmation_state(e)==[1,1]
    e.press('Left',30)
    assert confirmation_state(e)==[1,0]
    e.press('Right',30)
    e.screenshot(name+'-confirm-no.png')
    e.press('A',60)
    assert menu_state(e)==[1,1,1,group] and confirmation_state(e)[0]==0, 'No must preserve the highlighted card'
    e.press('A',60)
    assert confirmation_state(e)==[1,0], 'A new prompt must start on Yes'
    e.press('B',60)
    assert menu_state(e)==[1,1,1,group] and confirmation_state(e)[0]==0, 'B must dismiss only the confirmation'
    assert e.read(0x03002A60,0x31C)==progress and e.savedata()==flash
    e.press('B',120)
    assert e.state().get('substate')==2 and menu_state(e)[0]==0
    for before,(address,size) in zip(vram,((0x06008000,0x2000),(0x0600C800,0x800),(0x050001C0,32))):
        assert e.read(address,size)==before, f'Menu failed to restore video memory at {address:08x}'
    assert e.read(0x03002A60,0x31C)==progress and e.savedata()==flash
    open_menu(e,group,hold=False)
    e.press('Down',30)
    e.press('B',120)
    open_menu(e,group,hold=False)
    assert menu_state(e)[1]==0, 'Every fresh menu must start on the first card'
    e.press('B',120)


def select_card(e,group,index):
    open_menu(e,group,hold=False)
    if index:e.press('Down',30)
    confirm_selected(e)
    spec=GROUP_CATALOG[group]['cards'][index]
    assert e.number(0x030039FA,2)==spec['id'], (e.state(),e.number(0x030039FA,2))
    assert menu_state(e)[0]==0


def confirm_selected(e):
    progress=e.read(0x03002A60,0x31C)
    flash=e.savedata()
    e.press('A',10)
    assert confirmation_state(e)==[1,0] and menu_state(e)[0]==1
    assert e.read(0x03002A60,0x31C)==progress and e.savedata()==flash, 'Selecting a card applied it before Yes'
    e.press('A',10)
    assert menu_state(e)[0]==0


def power_prompt(e):
    for _ in range(100):
        if e.state().get('substate')==32:
            e.run(60)
            return
        e.press('A',30)
    raise AssertionError(f'Power-Up reward did not reach repeat prompt: {e.state()}')


def finish_power(e,back_state):
    power_prompt(e)
    e.press('Right',30)
    e.press('A',600)
    assert e.state()['state']==back_state


def watch(e,group,index,back_state):
    before=e.read(0x03002A60,0x31C)
    flash=e.savedata()
    world_lives=e.number(0x03003A48,2) if group==2 else None
    spec=GROUP_CATALOG[group]['cards'][index]
    card=decoded_card((ROOT/'eReader_cards'/GROUP_CATALOG[group]['folder']/spec['file']).read_bytes())
    replay=card[PAYLOAD+12:].ljust(0x800,b'\0')
    select_card(e,group,index)
    assert e.number(0x030039AD)==4 and e.number(0x030039F6)==group
    assert e.read(0x020143D0,0x800)==replay and e.read(0x02013BD0,0x800)==replay
    e.until(lambda s:s['state']==0x1A)
    start=e.frames
    levels=set()
    while e.state()['state']!=back_state and e.frames-start<30000:
        if e.state()['state']==0x1A:levels.add(e.number(0x03003B08,2))
        elapsed=e.frames-start
        if elapsed in (120,1200):e.screenshot(f'playback-{group}-{index}-{elapsed}.png')
        e.run(30)
    assert e.state()['state']==back_state, f'Demo failed to return: {spec["file"]}'
    e.run(90)
    after=e.read(0x03002A60,0x31C)
    expected=bytearray(before)
    if group==2:
        # Native World-e playback return synchronizes its active lives into the
        # inactive normal-file mirror. A World-e 5-Up exposes this bookkeeping.
        assert e.number(0x03003A48,2)==world_lives, 'Demo changed active World-e lives'
        expected[10:12]=world_lives.to_bytes(2,'little')
    assert after==expected, 'Demo changed save-file RAM: '+str([(hex(0x03002A60+i),x,y) for i,(x,y) in enumerate(zip(expected,after)) if x!=y])
    assert e.savedata()==flash, 'Demo changed cartridge save memory'
    e.screenshot(f'returned-{group}-{index}.png')
    return {'card':spec['file'],'id':spec['id'],'playback_frames':e.frames-start-90,'sublevels':sorted(levels)}


def run_context(core,rom,artifacts,world_e=False):
    e=Emulator(core,rom,artifacts)
    runs=[]
    try:
        boot_to_file_select(e)
        if world_e:
            for _ in range(3):e.press('Down',60)
            e.press('A',600)
        else:
            for _ in range(2):e.press('A',300)
        back_state=0x4C if world_e else 0x11
        assert e.state()['state']==back_state
        selected_powerup=False
        def scan(powerup=False):
            nonlocal selected_powerup
            # World-e rebuilds its map object on return, resetting the native
            # two-choice R popup to Demo. The main-game popup remembers its row.
            if world_e:selected_powerup=False
            e.press('R')
            if powerup!=selected_powerup:
                e.press('Down')
                selected_powerup=powerup
            e.press('A',600)
            assert e.state().get('substate')==2

        # The Power-Up list uses the same controller in both worlds.
        scan(True)
        parity(e,0,'powerup')
        life_address=0x03003A48 if world_e else 0x03002A6A
        lives=e.number(life_address,2)
        inventory=e.inventory()
        select_card(e,0,1)
        e.until(lambda s:e.number(life_address,2)==lives+5)
        assert e.inventory()==inventory
        e.screenshot('five-up-granted.png')
        # Yes opens a fresh selector; it must not apply another card.
        power_prompt(e)
        e.press('A',120)
        assert menu_state(e)==[1,0,1,0] and e.number(life_address,2)==lives+5
        confirm_selected(e)
        e.run(300)
        assert e.inventory()==ITEMS+bytes(26)
        finish_power(e,back_state)

        group=2 if world_e else 1
        scan()
        parity(e,group,'demo')
        # Cancel twice returns to the originating map and applies neither card.
        e.press('B',600)
        assert e.state()['state']==back_state
        for index in (0,1):
            scan()
            runs.append(watch(e,group,index,back_state))
        return {'passed':True,'frames':e.frames,'demos':runs,
                'checks':['shared input behavior','held OK cannot activate','idle causes no effects',
                          'Up/Down wrap equally','held navigation moves once','unmapped buttons ignored',
                          'B returns to OK','second B returns to map','selection resets on reopen',
                          'held selection cannot confirm','No and B preserve selected row',
                          'Left/Right choose Yes/No','only explicit Yes activates',
                          'native HUD graphics and palette intact','video memory restored','only applicable Demo IDs/payloads',
                          'both Demo selections complete without input','progress and save memory preserved',
                          '5-Up grants exactly five lives','repeat prompt opens selector',
                          '10-Item Set still grants ten items']}
    finally:e.save_trace();e.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,default=Path('C:/RetroBat/emulators/retroarch/cores/mgba_libretro.dll'))
    p.add_argument('--rom',type=Path,default=ROOT/'output/sma4-card-menu-test.gba')
    p.add_argument('--artifacts',type=Path,default=ROOT/'output/verification-card-menu')
    args=p.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    result_path=args.artifacts/'result.json'
    result_path.unlink(missing_ok=True)
    original=ROOT/'original_roms'/ROM_NAME
    assert sha256(original.read_bytes())==ROM_SHA256
    report={'passed':True,'rom_sha256':sha256(args.rom.read_bytes()),
            'normal':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts.resolve()/'normal'),
            'world_e':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts.resolve()/'world-e',True),
            'memory_writes_by_test':0}
    assert sha256(original.read_bytes())==ROM_SHA256
    result_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
