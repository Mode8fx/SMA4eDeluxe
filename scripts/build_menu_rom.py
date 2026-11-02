"""Build the shared six-card selection-menu test. Requires Python 3 and Clang."""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess

from arm_object import link
from build_test_rom import ROOT, ROM_NAME, ROM_SHA256, PAYLOAD, sha256
from menu_style import make_assets

CODE = 0x770000
FONT = 0x774000
MAPS = 0x798000
PALETTE = 0x7A2000
GROUPS = 0x7A2100
DESCRIPTORS = 0x7A2200
BASE = 0x08000000
HOOK = 0x23EE90

GROUP_CATALOG = [
    {'title':'POWER-UP CARDS', 'folder':'Power-Up', 'cards':[
        {'file':'[Japan] [25] 10-Item Set.bin',
         'name':'10-Item Set',
         'sha256':'351f8f546ae1cd6148f5df9e2471d0c5d80ba989d095bc831faaa08e007ad5e6','id':31},
        {'file':'[USA] [17] 5-Up Mushroom (USA).bin',
         'name':'5-Up Mushroom',
         'sha256':'09ea91d5436561b558e42f400786a873eb491291d266279cdf34187d90806f05','id':83}]},
    {'title':'DEMO CARDS - NORMAL','folder':'Demo','cards':[
        {'file':'[Japan] [16] World 8-Fortress Speed Stage.bin',
         'name':'8-Fortress Speed Run',
         'sha256':'34d4257ed813cba5acc5d6dae82585e2c3c3219221c3819b7d9739215697cd26','id':16},
        {'file':'[Japan] [24] World 7-3 Star Power.bin',
         'name':'World 7-3 Star Power',
         'sha256':'184d6c0f676230c2e0de1369850bbad4e0bcd1a0dd5a53cf5cb78911b10e8757','id':24}]},
    {'title':'DEMO CARDS - WORLD-E','folder':'Demo (World-e)','cards':[
        {'file':'[Japan] [102] World-e Bombarded by Bob-ombs All Coins.bin',
         'name':'Bombarded All Coins',
         'sha256':'f513f2cf16c11da26fe93b739cf11278f5fecbddaa85760717a252ba13cacd18','id':102},
        {'file':'[Japan] [101] World-e Classic World 1-1 Unlimited 1-Ups.bin',
         'name':'Classic 1-1 1-Ups',
         'sha256':'69c3a27c926b8e21c2a2925353975711648465fc0d5898060e7e1716b26f1851','id':101}]},
]


def decoded_card(raw):
    if len(raw) == 0x840 and raw[0x1A:0x22] == b'NINTENDO': return raw
    if len(raw) != 0xB60: raise ValueError('Unsupported card envelope')
    # NEDC long strip: discard two header bytes per 104-byte block, then
    # deinterleave 44 RS codewords, retaining each codeword's 48 data bytes.
    # Inputs are SHA-256 locked, so correction of damaged scans is not needed.
    stream = b''.join(raw[i+2:i+104] for i in range(0,len(raw),104))[:64*44]
    decoded = bytes(stream[row*44+col] for col in range(44) for row in range(48))
    if decoded[0x1A:0x22] != b'NINTENDO': raise ValueError('Invalid raw card envelope')
    return decoded


def build(rom, output, compiler):
    original = rom.read_bytes()
    if sha256(original) != ROM_SHA256: raise ValueError('Expected original USA Rev 1 VC ROM')
    if ROOT/'original_roms' in output.parents or ROOT/'eReader_cards' in output.parents or output==rom:
        raise ValueError('Output must not overwrite source ROM/cards')
    if any(original[CODE:0x7FC000]): raise ValueError('Reserved injection area is occupied')
    output.parent.mkdir(parents=True,exist_ok=True)
    work = output.parent/'build-menu'
    work.mkdir(exist_ok=True)
    patched=bytearray(original)
    writes=[]
    def put(offset,data):
        for start,end in writes:
            if offset<end and start<offset+len(data): raise ValueError('Overlapping patch data')
        writes.append((offset,offset+len(data)))
        patched[offset:offset+len(data)]=data
    groups=[]
    index=0
    for group_id,definition in enumerate(GROUP_CATALOG):
        cards=[]
        descriptor=bytearray()
        if not 1<=len(definition['cards'])<=100:raise ValueError('Menu IDs must be in 1..100')
        for menu_id,spec in enumerate(definition['cards'],1):
            if not 1<=len(spec['name'])<=20:raise ValueError('Card names must contain 1..20 characters')
            region='J' if spec['file'].startswith('[Japan]') else 'U' if spec['file'].startswith('[USA]') else None
            if region is None:raise ValueError('Card region must be J or U')
            path=ROOT/'eReader_cards'/definition['folder']/spec['file']
            raw=path.read_bytes()
            if sha256(raw)!=spec['sha256']: raise ValueError(f'Unexpected card: {path}')
            decoded=decoded_card(raw)
            header=int.from_bytes(decoded[PAYLOAD:PAYLOAD+2],'little')
            if header&511!=spec['id'] or header>>9!=(3 if group_id==0 else 4):
                raise ValueError('Unexpected game header')
            if group_id and decoded[PAYLOAD+9]!=group_id:
                raise ValueError('Demo applicability does not match catalog')
            bin_offset=0x7F0000+index*0x1000
            put(bin_offset,raw)
            canonical=bin_offset
            if raw!=decoded:
                canonical=0x7F6000
                put(canonical,decoded)
            transfer=0
            if group_id:
                transfer=0x7F8000+(index-2)*0x1000
                put(transfer,decoded[PAYLOAD:].ljust(0x80C,b'\0'))
            descriptor+=struct.pack('<II',BASE+canonical+PAYLOAD,BASE+transfer if transfer else 0)
            cards.append({**spec,'menu_id':menu_id,'region':region,'path':str(path),'bin_rom_offset':hex(bin_offset),
                          'decoded_sha256':sha256(decoded),'payload_address':hex(BASE+canonical+PAYLOAD),
                          'transfer_address':hex(BASE+transfer) if transfer else None})
            index+=1
        put(DESCRIPTORS+group_id*16,descriptor)
        put(GROUPS+group_id*16,struct.pack('<IIII',BASE+DESCRIPTORS+group_id*16,len(cards),
                                        BASE+MAPS+group_id*0x3000,BASE+FONT+group_id*0xC000))
        groups.append({**definition,'cards':cards})
    font,palette,maps,style_info=make_assets(groups,original)
    put(FONT,font);put(PALETTE,palette)
    for i,data in enumerate(maps):put(MAPS+i*0x800,data)
    (work/'menu_assets.h').write_text(f'#define MENU_FONT_ADDRESS 0x{BASE+FONT:08X}\n#define MENU_PALETTE_ADDRESS 0x{BASE+PALETTE:08X}\n#define MENU_GROUPS_ADDRESS 0x{BASE+GROUPS:08X}\n')
    objects=[]
    for name in ('card_menu','card_menu_view'):
        obj=work/(name+'.o')
        subprocess.run([compiler,'--target=arm-none-eabi','-mcpu=arm7tdmi','-mthumb','-Os',
                        '-ffreestanding','-fno-builtin','-fno-jump-tables','-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables','-Wall','-Wextra','-Werror','-I',str(work),
                        '-c',str(ROOT/'scripts'/(name+'.c')),'-o',str(obj)],check=True)
        objects.append(obj.read_bytes())
    code,symbols=link(objects,BASE+CODE)
    if len(code)>FONT-CODE: raise ValueError('Code exceeds reserved region')
    put(CODE,code)
    if original[HOOK:HOOK+4]!=struct.pack('<I',0x080C1039):raise ValueError('Unexpected dispatch table')
    put(HOOK,struct.pack('<I',symbols['card_menu_tick']))
    # Extend the communication object from 0x1F4 to 0x208 bytes, including its clear.
    if original[0xC06E4:0xC06E8]!=struct.pack('<HH',0x21FA,0x0049):raise ValueError('Unexpected allocation code')
    put(0xC06E4,struct.pack('<HH',0x2182,0x0089))
    instruction=struct.unpack_from('<H',original,0xC06F4)[0]
    if instruction&0xFF00!=0x4A00:raise ValueError('Unexpected object clear instruction')
    pool=((0xC06F4+4)&~3)+(instruction&255)*4
    if original[pool:pool+4]!=struct.pack('<I',0x010000FA):raise ValueError('Unexpected object clear count')
    put(pool,struct.pack('<I',0x01000104))
    output.write_bytes(patched)
    report={'input_rom':str(rom),'input_sha256':sha256(original),'output_rom':str(output),
            'output_sha256':sha256(patched),'rom_size':len(patched),'code_size':len(code),
            'symbols':{k:hex(v) for k,v in symbols.items()},'groups':groups,'style':style_info,
            'patch_regions':[[hex(a),hex(b)] for a,b in writes],
            'compiler':subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    assert rom.read_bytes()==original
    print(json.dumps({'output':str(output),'sha256':report['output_sha256'],'code_size':len(code),'cards':index},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,default=ROOT/'original_roms'/ROM_NAME)
    p.add_argument('--output',type=Path,default=ROOT/'output/sma4-card-menu-test.gba')
    p.add_argument('--compiler',default=shutil.which('clang'))
    args=p.parse_args()
    if not args.compiler:p.error('Clang is required; pass --compiler PATH')
    build(args.rom.resolve(),args.output.resolve(),args.compiler)


if __name__=='__main__':main()
