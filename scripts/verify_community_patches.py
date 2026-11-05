"""Check all optional IPS combinations against the current e-Deluxe build.

Fixtures are applied to ROM bytes in memory. Source ROMs and patches are preserved.
"""
import argparse
import itertools
import json
import struct
from pathlib import Path

from build_rom import DEFAULT_OUTPUT, ROOT,ROM_NAME,ROM_SHA256,PAYLOAD,sha256,GROUP_CATALOG,decoded_card
from emulator import DEFAULT_CORE,Emulator,boot_to_map
from verify_menu_rom import (parity,open_menu,snapshot,restore,select_card,
                             power_prompt,finish_power,watch,run_context)
from verify_menu_palette import adjusted_palette
from ips import read_ips,apply_ips


PATCHES=(('bugs','Bug fixes [Mario_GMD] (v1.0B).ips'),
         ('rumble','Cartridge Rumble [djedditt] (v1.0).ips'),
         ('palette','GBP SNES Palette Hack [djedditt] (v1.0).ips'),
         ('voices','Voice Removal [mangaman3000] (v0.2).ips'))


def static_checks(image,manifest,patches):
    assert sha256(image)==manifest['output_sha256']
    original=(ROOT/'original_roms'/ROM_NAME).read_bytes()
    assert sha256(original)==ROM_SHA256
    ranges=[(int(a,16),int(b,16)) for a,b in manifest['patch_regions']]
    writes={}
    details={}
    for key,name in PATCHES:
        values={}
        for address,data in patches[key]:
            assert not any(address<end and start<address+len(data) for start,end in ranges),(key,'e-Deluxe overlap')
            assert image[address:address+len(data)]==original[address:address+len(data)],key
            for i,value in enumerate(data):
                assert address+i not in values or values[address+i]==value
                values[address+i]=value
        writes[key]=values
        details[key]={'file':name,'sha256':sha256((ROOT/'community_patches'/name).read_bytes()),
                      'records':len(patches[key]),'bytes_written':len(values),
                      'highest_offset':hex(max(values)),'e_deluxe_overlap':0,'truncation':False}
    for a,b in itertools.combinations(writes,2):
        assert not writes[a].keys()&writes[b].keys(),(a,b,'community overlap')
    subsets=[]
    orders=0
    keys=list(patches)
    for size in range(len(keys)+1):
        for subset in itertools.combinations(keys,size):
            hashes=set()
            for order in itertools.permutations(subset):
                hashes.add(sha256(apply_ips(image,*(patches[key] for key in order))))
                orders+=1
            assert len(hashes)==1,subset
            subsets.append({'patches':list(subset),'sha256':hashes.pop(),'rom_size':len(image)})
    return {'passed':True,'patches':details,'subsets':subsets,'application_orders':orders,
            'all_orders_identical':True,'all_community_pairs_disjoint':True}


def matrix_context(core,rom,image,artifacts,world_e,mode):
    e=Emulator(core,rom,artifacts,rom_bytes=image)
    try:
        back=boot_to_map(e,world_e)
        assert e.number(0x0300378B)&6==mode
        baseline=snapshot(e)
        power=3 if world_e else 0
        expected=struct.pack('<16H',*adjusted_palette((ROOT/'original_roms'/ROM_NAME).read_bytes(),mode))
        for group in (power,2 if world_e else 1):
            restore(e,baseline);e.press('R')
            if group==power:e.press('Down')
            e.press('A',600)
            ready=snapshot(e)
            open_menu(e,group,False)
            assert e.read(0x050001C0,32)==expected
            e.press('B',120)
            parity(e,group,'powerup' if group==power else 'demo')
            restore(e,ready)
            if group==power:
                card=GROUP_CATALOG[group]['cards'][20]
                assert card['name']=='10-Item Set'
                data=decoded_card((ROOT/'eReader_cards'/card['folder']/card['file']).read_bytes())[PAYLOAD:]
                select_card(e,group,20);power_prompt(e)
                assert e.inventory()==data[12:22].ljust(36,b'\0')
                finish_power(e,back)
                restore(e,ready)
                flags=e.number(0x03002D52,2)
                select_card(e,group,26);power_prompt(e)
                assert e.number(0x03002D52,2)==flags^0x400
                finish_power(e,back)
            else:
                demo=watch(e,group,0,back)
        return {'passed':True,'frames':e.frames,'palette_mode':mode,'demo':demo,
                'checks':['all rows/pages/confirmations','Up/Down page edges and page wrap',
                          'native palette and video restoration','Switch status colours',
                          '10-Item Set reward','Blue Switch native activation',
                          'complete Demo playback and save preservation']}
    finally:e.save_trace();e.close()


def reset_check(core,rom,image,artifacts,from_menu):
    e=Emulator(core,rom,artifacts,rom_bytes=image)
    try:
        boot_to_map(e)
        if from_menu:
            e.press('R');e.press('Down');e.press('A',600)
            open_menu(e,0,False)
        # Native main-loop check at 080006FC accepts exactly A+B+Select+Start.
        e.run(2,'A','B','Select','Start');e.run(90)
        assert e.state()['state']!=0x11 or e.state()['mode']!=1
        for _ in range(15):
            if e.state()['mode']==1 and e.state()['state']==9:break
            e.press('A',180)
        assert e.state()['mode']==1 and e.state()['state']==9,e.state()
        e.screenshot('reset-file-select.png')
        return {'passed':True,'from_menu':from_menu,'returned_to_file_select':True}
    finally:e.save_trace();e.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,default=DEFAULT_CORE)
    p.add_argument('--rom',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--artifacts',type=Path,default=ROOT/'output/verification-community-patches')
    p.add_argument('--scope',choices=('all','matrix','full','reset'),default='all')
    args=p.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    result=args.artifacts/('result.json' if args.scope=='all' else args.scope+'-result.json')
    result.unlink(missing_ok=True)
    image=args.rom.read_bytes()
    manifest=json.loads(args.rom.with_suffix('.json').read_text())
    patches={key:read_ips(ROOT/'community_patches'/name) for key,name in PATCHES}
    report={'passed':True,'base_rom_sha256':sha256(image),'scope':args.scope,
            'static':static_checks(image,manifest,patches),'memory_writes_by_test':0,
            'fixtures_created_in_memory':True}
    if args.scope in ('all','matrix'):
        report['matrix']=[]
        for subset in report['static']['subsets']:
            keys=subset['patches'];name='-'.join(keys) or 'base'
            print('Checking '+name,flush=True)
            fixture=apply_ips(image,*(patches[key] for key in keys))
            case={**subset,'normal':matrix_context(args.core.resolve(),args.rom.resolve(),fixture,args.artifacts/name/'normal',False,0 if 'palette' in keys else 2),
                  'world_e':matrix_context(args.core.resolve(),args.rom.resolve(),fixture,args.artifacts/name/'world-e',True,0 if 'palette' in keys else 2)}
            report['matrix'].append(case)
    if args.scope in ('all','full'):
        fixture=apply_ips(image,*patches.values())
        report['full_catalog']={'rom_sha256':sha256(fixture),
            'normal':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts/'full-catalog'/'normal',False,rom_bytes=fixture),
            'world_e':run_context(args.core.resolve(),args.rom.resolve(),args.artifacts/'full-catalog'/'world-e',True,rom_bytes=fixture)}
    if args.scope in ('all','reset'):
        report['soft_reset']=[]
        for name,keys in (('base',()),('rumble',('rumble',)),('all',tuple(patches))):
            for from_menu in (False,True):
                print('Soft reset: '+name+(' menu' if from_menu else ' map'),flush=True)
                report['soft_reset'].append({'configuration':name,**reset_check(
                    args.core.resolve(),args.rom.resolve(),apply_ips(image,*(patches[key] for key in keys)),
                    args.artifacts/'soft-reset'/name/('menu' if from_menu else 'map'),from_menu)})
    assert args.rom.read_bytes()==image
    for key,name in PATCHES:
        assert sha256((ROOT/'community_patches'/name).read_bytes())==report['static']['patches'][key]['sha256']
    result.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'scope':args.scope,'report':str(result)},indent=2))


if __name__=='__main__':main()
