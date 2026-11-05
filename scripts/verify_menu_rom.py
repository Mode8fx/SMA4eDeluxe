"""Input-driven mGBA checks for every embedded card and all menu contexts."""
import argparse
import ctypes as C
import json
from pathlib import Path

from build_rom import DEFAULT_OUTPUT, ROOT, ROM_NAME, ROM_SHA256, PAYLOAD, sha256, GROUP_CATALOG, decoded_card
from emulator import DEFAULT_CORE, Emulator, boot_to_map
from menu_style import NativeFont, description_lines, DESCRIPTION_COLOR


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


def assert_switch_label(e,index,label):
    """Read the rendered pixels, including colour, rather than only flag RAM."""
    font=NativeFont((ROOT/'original_roms'/ROM_NAME).read_bytes())
    color={'ON':11,'OFF':12,'N/A':2}[label]
    expected=[[0]*24 for _ in range(8)]
    font.draw(expected,24-font.width(label),0,label,color=color)
    for y in range(8):
        py=1+(index%8)*11+y
        for x in range(24):
            address=0x0600C000+((py//8)*3+x//8)*32+(py%8)*4+(x%8)//2
            pixel=(e.number(address)>>(4*(x%2)))&15
            assert (pixel if pixel in (2,11,12) else 0)==expected[y][x], (index,label,x,y,pixel)
    palette=e.number(0x050001C0+color*2,2)
    if label=='ON':assert palette&31==0 and palette>>10==0 and (palette>>5)&31
    elif label=='OFF':assert palette>>5==0 and palette&31
    else:assert palette==0


def highlight(e,index):
    for _ in range(index//8):e.press('Right',5)
    for _ in range(index%8):e.press('Down',5)


def assert_confirmation_description(e,card):
    """Check displayed tile pixels and blue palette after the bank switch."""
    font=NativeFont((ROOT/'original_roms'/ROM_NAME).read_bytes())
    lines=description_lines(card,font)
    top=44-(11*len(lines))//2
    sx=(e.number(0x03003750,2)>>3)&31
    sy=(e.number(0x03003754,2)>>3)&31
    for i,line in enumerate(lines):
        assert len(line)<=35
        expected=[[0]*240 for _ in range(8)]
        font.draw(expected,(240-font.width(line))//2,0,line,color=DESCRIPTION_COLOR)
        for row in range(8):
            y=top+35+11*i+row
            for x in range(24,216):
                cell=e.number(0x0600C800+((((y//8+sy)&31)*32)+((x//8+sx)&31))*2,2)
                address=0x06008000+(cell&1023)*32+(y%8)*4+(x%8)//2
                pixel=(e.number(address)>>(4*(x%2)))&15
                assert (pixel==DESCRIPTION_COLOR)==(expected[row][x]==DESCRIPTION_COLOR), (card['name'],line,x,y,pixel)
    if lines:
        palette=e.number(0x050001C0+DESCRIPTION_COLOR*2,2)
        assert palette&31==0 and (palette>>10)>((palette>>5)&31), hex(palette)


def parity(e, group, name):
    """Visit every real row/page and confirmation, preserving native video/state."""
    cards=GROUP_CATALOG[group]['cards']
    progress=e.read(0x03002A60,0x31C)
    flash=e.savedata()
    video=[(a,e.read(a,n)) for a,n in ((0x06008000,0x4800),(0x0600C800,0x800),(0x050001C0,32))]
    native_tiles=e.read(0x06000000,0x8000)
    hud=e.read(0x050001E0,32)
    open_menu(e,group)
    e.run(60,'Down');e.run(10)
    assert menu_state(e)[1]==1, 'Held navigation moved more than once'
    e.press('Up',5);e.press('Up',5)
    assert menu_state(e)[1]==0
    # Page movement preserves the row and wraps.
    if len(cards)>8:
        e.press('Left',5)
        assert menu_state(e)[1]==((len(cards)-1)//8)*8
        e.press('Right',5)
        assert menu_state(e)[1]==0
    e.press('Start',5)
    assert menu_state(e)[1]==0
    for i,card in enumerate(cards):
        assert menu_state(e)==[1,i,1,group]
        if i%8==0:
            e.press('Up',5)
            assert menu_state(e)[1]==i, 'Up crossed the top of the page'
        screen=e.number(0x030075E0,4)
        assert e.number(screen+0x205)==i//8
        if card['folder']=='Switch':
            compatible=card['applicability'] in (0,GROUP_CATALOG[group]['context'])
            assert_switch_label(e,i,'OFF' if compatible else 'N/A')
        if i%8==0:e.screenshot(f'{name}-page-{i//8+1}.png')
        e.run(5,'A')
        assert confirmation_state(e)==[1,0] and menu_state(e)[2]==0
        e.run(5)
        assert_confirmation_description(e,card)
        e.screenshot(f'{name}-confirm-{i:02}.png')
        e.press('Right',5)
        assert confirmation_state(e)==[1,1]
        assert_confirmation_description(e,card)
        if i==0:
            e.press('A',5)
        else:e.press('B',5)
        assert menu_state(e)==[1,i,1,group] and confirmation_state(e)[0]==0
        if i%8==7 or i==len(cards)-1:
            e.press('Down',5)
            assert menu_state(e)[1]==i, 'Down crossed the bottom of the page'
            if i+1<len(cards):
                e.press('Right',5)
                while menu_state(e)[1]%8:e.press('Up',5)
        else:e.press('Down',5)
    assert menu_state(e)[1]==len(cards)-1
    assert e.read(0x03002A60,0x31C)==progress and e.savedata()==flash
    assert e.read(0x06000000,0x8000)==native_tiles
    assert e.read(0x050001E0,32)==hud
    e.press('B',120)
    assert e.state()['substate']==2 and menu_state(e)[0]==0
    for address,before in video:assert e.read(address,len(before))==before
    open_menu(e,group,hold=False)
    assert menu_state(e)[1]==0
    e.press('B',120)


def snapshot(e):
    e.lib.retro_serialize_size.restype=C.c_size_t
    data=C.create_string_buffer(e.lib.retro_serialize_size())
    e.lib.retro_serialize.argtypes=[C.c_void_p,C.c_size_t]
    e.lib.retro_serialize.restype=C.c_bool
    assert e.lib.retro_serialize(data,len(data))
    return data.raw


def restore(e,data):
    e.lib.retro_unserialize.argtypes=[C.c_void_p,C.c_size_t]
    e.lib.retro_unserialize.restype=C.c_bool
    assert e.lib.retro_unserialize(C.create_string_buffer(data),len(data))
    e.run(1)


def select_card(e,group,index):
    open_menu(e,group,hold=False)
    highlight(e,index)
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
    state=menu_state(e)
    e.screenshot(f'scan-confirm-{state[3]}-{state[1]}.png')
    e.press('A',10)
    assert menu_state(e)[0]==0


def power_prompt(e):
    for _ in range(1000):
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
    card=decoded_card((ROOT/'eReader_cards'/spec.get('folder',GROUP_CATALOG[group]['folder'])/spec['file']).read_bytes())
    replay=card[PAYLOAD+12:].ljust(0x800,b'\0')
    select_card(e,group,index)
    assert e.number(0x030039AD)==4 and e.number(0x030039F6)==group
    assert e.read(0x020143D0,0x800)==replay and e.read(0x02013BD0,0x800)==replay
    e.until(lambda s:s['state']==0x1A)
    start=e.frames
    levels=set()
    while e.state()['state']!=back_state and e.frames-start<120000:
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
        # Native World-e playback (080C1D94) selects the recording's character
        # from replay header bit 14 and leaves that selection on return. Castle
        # Dash is a Luigi recording; this is a character selection, not progress.
        expected[0x317]=(int.from_bytes(replay[6:8],'little')>>14)&1
    assert after==expected, 'Demo changed save-file RAM: '+str([(hex(0x03002A60+i),x,y) for i,(x,y) in enumerate(zip(expected,after)) if x!=y])
    assert e.savedata()==flash, 'Demo changed cartridge save memory'
    e.screenshot(f'returned-{group}-{index}.png')
    return {'card':spec['file'],'id':spec['id'],'playback_frames':e.frames-start-90,'sublevels':sorted(levels)}


def run_context(core,rom,artifacts,world_e=False,rom_bytes=None):
    e=Emulator(core,rom,artifacts,rom_bytes=rom_bytes)
    try:
        back_state=boot_to_map(e,world_e)
        baseline=snapshot(e)
        power_group=3 if world_e else 0
        demos=[]
        rewards=[]
        for group in (power_group,2 if world_e else 1):
            restore(e,baseline)
            e.press('R')
            if group==power_group:e.press('Down')
            e.press('A',600)
            ready=snapshot(e)
            parity(e,group,'powerup' if group==power_group else 'demo')
            for index,spec in enumerate(GROUP_CATALOG[group]['cards']):
                restore(e,ready)
                print(('world-e' if world_e else 'normal')+f' group {group} card {index+1}: '+spec['file'],flush=True)
                if group!=power_group:
                    demos.append(watch(e,group,index,back_state))
                    continue
                path=ROOT/'eReader_cards'/spec['folder']/spec['file']
                data=decoded_card(path.read_bytes())[PAYLOAD:]
                subtype=data[8]
                count=int.from_bytes(data[9:11],'little')
                life_address=0x03003A48 if world_e else 0x03002A6A
                lives=e.number(life_address,2)
                inventory=e.inventory()
                flags=e.number(0x03002D52,2)
                switch_address=0x03003A8B if world_e else 0x03002D64
                switches=e.number(switch_address)
                select_card(e,group,index)
                assert e.number(0x030039AD)==spec['type']
                assert e.number(0x030039CF)==subtype and e.number(0x030039D0,2)==count
                power_prompt(e)
                compatible=spec['applicability'] in (0,GROUP_CATALOG[group]['context'])
                if not compatible:
                    assert spec['folder']=='Switch'
                    assert e.number(0x03002D52,2)==flags and e.number(switch_address)==switches
                    assert e.inventory()==inventory and e.number(life_address,2)==lives
                    e.screenshot(f'incompatible-switch-{index}.png')
                elif subtype==0:
                    assert e.inventory()==data[12:12+count].ljust(36,b'\0'), (spec,e.inventory().hex())
                elif subtype==1:
                    assert e.number(life_address,2)==min(998,lives+count), (spec,e.number(life_address,2),lives,count)
                    assert e.inventory()==inventory
                else:
                    masks={2:0x400,3:0x40,4:1,5:0x100,6:0x10,7:2,8:0x2A80}
                    assert e.number(0x03002D52,2)==flags^masks[subtype], (spec,hex(e.number(0x03002D52,2)),hex(flags))
                    assert e.number(switch_address)==switches^(1<<(subtype-2))
                    assert e.inventory()==inventory
                rewards.append({'card':spec['file'],'id':spec['id'],'type':spec['type'],
                                'subtype':subtype,'count':count,'compatible':compatible})
                if spec['folder']=='Switch':
                    # Native repeat Yes must reopen with the current saved state.
                    e.press('A',120)
                    assert menu_state(e)==[1,0,1,group]
                    highlight(e,index)
                    assert_switch_label(e,index,'ON' if compatible else 'N/A')
                    e.screenshot(f'switch-after-scan-{index}.png')
                    if compatible:
                        confirm_selected(e)
                        power_prompt(e)
                        assert e.number(0x03002D52,2)==flags and e.number(switch_address)==switches
                        e.press('A',120)
                        highlight(e,index)
                        assert_switch_label(e,index,'OFF')
                        e.screenshot(f'switch-after-second-scan-{index}.png')
                    e.press('B',120);e.press('B',600)
                    assert e.state()['state']==back_state
                else:finish_power(e,back_state)
        return {'passed':True,'frames':e.frames,'powerups':rewards,'demos':demos,
                'checks':['every row and confirmation in each context','Up/Down stop at every page edge',
                          'Left/Right page boundaries and wrap',
                          'held buttons cannot select/confirm twice','No and B preserve row/page',
                          'blue descriptions render completely for both Yes/No choices',
                          'native video restored on cancellation','all applicable Power-Up rewards',
                          'Switch ON/OFF rendered colours and repeat updates','N/A visible and scan attempt allowed',
                          'compatible Switch toggles ON then OFF','100-Up promotional type 2',
                          'all applicable Demo payloads and complete playback','Demo progress/save preservation',
                          'native emulator savestates isolate each card']}
    finally:e.save_trace();e.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,default=DEFAULT_CORE)
    p.add_argument('--rom',type=Path,default=DEFAULT_OUTPUT)
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
    print(json.dumps({'passed':True,'rom_sha256':report['rom_sha256'],
                      'powerup_applications':sum(len(report[c]['powerups']) for c in ('normal','world_e')),
                      'complete_demos':sum(len(report[c]['demos']) for c in ('normal','world_e')),
                      'report':str(result_path)},indent=2))


if __name__=='__main__':main()
