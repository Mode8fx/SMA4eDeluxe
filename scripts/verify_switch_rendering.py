"""Check every video frame during Switch cursor and confirmation transitions."""
import argparse
import json
from pathlib import Path

from build_rom import DEFAULT_OUTPUT, ROOT, ROM_NAME, sha256, GROUP_CATALOG
from menu_style import NativeFont, confirmation_bounds
from verify_menu_rom import (open_menu, highlight, menu_state, confirmation_state,
                             assert_switch_label, power_prompt)
from emulator import DEFAULT_CORE, Emulator, boot_to_map


def strip_frame(e):
    pixels,width,height,pitch,fmt=e.video
    assert (width,height)==(240,160)
    size=4 if fmt==1 else 2
    return b''.join(pixels[y*pitch+176*size:y*pitch+200*size] for y in range(24,112))


def transition(e,button,name,unchanged=False):
    """An atomic transition may show either complete view, never a partial one."""
    before=strip_frame(e)
    samples=[]
    for i in range(10):
        e.run(1,*([button] if i<2 else []))
        samples.append(strip_frame(e))
    after=samples[-1]
    for i,sample in enumerate(samples):
        if sample not in (before,after) or (unchanged and sample!=before):
            e.screenshot(f'failure-{name}-{i}.png')
            raise AssertionError(f'Status strip flickered during {name}, frame {i}')
    return before,after,len(samples)


def check_phase(e,group,phase):
    cards=GROUP_CATALOG[group]['cards']
    font=NativeFont((ROOT/'original_rom'/ROM_NAME).read_bytes())
    frames=0
    highlight(e,26)
    # Both cursor directions on a page with OFF and N/A, and later ON on page 5.
    for repeat in range(12):
        for button in ('Down','Up'):
            _,_,count=transition(e,button,f'{phase}-cursor-{repeat}-{button}',True)
            frames+=count
    for _ in range(2):e.press('Up',5)
    assert menu_state(e)[1]==24
    for index in range(24,33):
        assert menu_state(e)[1]==index
        for row in range((index//8)*8,min((index//8)*8+8,len(cards))):
            card=cards[row]
            if card['folder']=='Switch':
                compatible=card['applicability'] in (0,GROUP_CATALOG[group]['context'])
                label=('ON' if phase=='on' and row in (26,32) else 'OFF') if compatible else 'N/A'
                assert_switch_label(e,row,label)
        before,after,count=transition(e,'A',f'{phase}-open-{index}')
        frames+=count
        assert confirmation_state(e)==[1,0]
        top,bottom=confirmation_bounds(cards[index],font)
        stride=len(before)//88
        # Visible rows must retain their exact pixels, including partial labels
        # cut by the popup edge. This checks the actual emulator framebuffer.
        for y in range(24,112):
            if not top<=y<bottom:
                offset=(y-24)*stride
                assert before[offset:offset+stride]==after[offset:offset+stride], (phase,index,y)
        if index in (24,32):e.screenshot(f'{phase}-confirm-{index}.png')
        for button in ('Right','Left'):
            _,_,count=transition(e,button,f'{phase}-answer-{index}-{button}',True)
            frames+=count
        _,restored,count=transition(e,'B',f'{phase}-cancel-{index}')
        frames+=count
        assert restored==before and confirmation_state(e)[0]==0
        if index<32:
            _,_,count=transition(e,'Right' if index==31 else 'Down',f'{phase}-next-{index}',index!=31)
            frames+=count
    return frames


def run_context(core,rom,artifacts,world_e):
    e=Emulator(core,rom,artifacts)
    try:
        back_state=boot_to_map(e,world_e)
        group=3 if world_e else 0
        e.press('R');e.press('Down');e.press('A',600)
        open_menu(e,group,hold=False)
        frames=check_phase(e,group,'off')
        # Scan Red through the native flow, so page 5 has a visible ON label
        # above the dialog. No RAM writes or existing save files are used.
        e.press('A',10);e.press('A',10)
        power_prompt(e)
        e.press('A',120)
        assert menu_state(e)==[1,0,1,group]
        # Also put an ON label on the multi-row Switch page for cursor tests.
        highlight(e,26)
        e.press('A',10);e.press('A',10)
        power_prompt(e)
        e.press('A',120)
        assert menu_state(e)==[1,0,1,group]
        frames+=check_phase(e,group,'on')
        e.press('B',120);e.press('B',600)
        assert e.state()['state']==back_state
        return {'passed':True,'video_frames_checked':frames,
                'checks':['Up/Down never clears labels','all nine final-page confirmations',
                          'ON/OFF/N/A outside popup preserved pixel-for-pixel',
                          'partial labels clipped at popup edge','Yes/No preserves strip',
                          'cancel restores strip','page transitions publish complete views']}
    finally:e.save_trace();e.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,default=DEFAULT_CORE)
    p.add_argument('--rom',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--artifacts',type=Path,default=ROOT/'output/verification-switch-rendering')
    args=p.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    result=args.artifacts/'result.json'
    result.unlink(missing_ok=True)
    report={'passed':True,'rom_sha256':sha256(args.rom.read_bytes()),
            'normal':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts/'normal',False),
            'world_e':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts/'world-e',True),
            'memory_writes_by_test':0}
    result.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
