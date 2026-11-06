"""Embed every eReader_cards BIN in the shared card menu. Requires Python 3 and Clang."""
import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
import shutil
import struct
import subprocess

from arm_object import link
from menu_style import make_assets, MAX_NAME_LENGTH

ROOT = Path(__file__).resolve().parents[1]
ROM_NAME = "Super Mario Advance 4 - Super Mario Bros. 3 (USA) (Rev 1) (Virtual Console).gba"
ROM_SHA256 = "c8db146071462572b3be6d2caefe453c584547515f9a2e3e75264cdf9243442e"
DEFAULT_OUTPUT = ROOT/'output/Super Mario Advance 4 - e-Deluxe.gba'
PAYLOAD = 0x72  # Start of the native game payload within a decoded card envelope.
CODE = 0x770000
GROUPS = 0x774000
ASSETS = 0x774100
BASE = 0x08000000
HOOK = 0x23EE90


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def filename_metadata(filename):
    match=re.fullmatch(r'\[(Japan|USA)\] \[(\d+|PR)\] (.+)\.bin',filename)
    if not match:raise ValueError(f'Unsupported card filename: {filename}')
    region,number,name=match.groups()
    # Redundant region suffixes are packaging metadata, not part of the title.
    name=re.sub(r' \((?:USA|Japan)\)$','',name)
    number=int(number) if number!='PR' else 'PR'
    if number!='PR' and not 1<=number<=999:raise ValueError('Filename IDs must fit the three-digit ID column')
    name=name.replace('_',':')
    return {'menu_id':number,'filename_id':number,'name':name[:MAX_NAME_LENGTH].rstrip(),
            'region':'JPN' if region=='Japan' else 'USA'}


def stage_order(card):
    match=re.search(r'\] W([1-8])-(\d+|Fortress)',card['file'])
    if not match:raise ValueError(f'Cannot determine Demo stage: {card["file"]}')
    world,stage=match.groups()
    world=int(world)
    position={2:5,5:5,8:3}[world] if stage=='Fortress' else int(stage)*2
    return world,position,card['filename_id'],card['region'],card['file']


def prepare_catalog(catalog):
    groups=deepcopy(catalog)
    for group in groups:
        if not 1<=len(group['cards'])<=100:raise ValueError('Each catalog must contain 1..100 cards')
        group['cards']=[{**spec,**filename_metadata(spec['file'])} for spec in group['cards']]
        if group['kind']=='demo' and group['context']==1:
            group['cards'].sort(key=stage_order)
            for index,card in enumerate(group['cards'],1):card['menu_id']=index
        else:
            group['cards'].sort(key=lambda card:(1000 if card['menu_id']=='PR' else card['menu_id'],card['region'],card['file']))
    return groups


def discover_catalog():
    definitions=[('POWER-UP CARDS',('Power-Up','Switch')),
                 ('DEMO CARDS - NORMAL',('Demo',)),
                 ('DEMO CARDS - WORLD-E',('Demo (World-e)',))]
    groups=[]
    included=set()
    for group_id,(title,folders) in enumerate(definitions):
        cards=[]
        for folder in folders:
            for path in sorted((ROOT/'eReader_cards'/folder).rglob('*.bin')):
                raw=path.read_bytes()
                decoded=decoded_card(raw)
                header=int.from_bytes(decoded[PAYLOAD:PAYLOAD+2],'little')
                cards.append({'file':path.name,'folder':str(path.parent.relative_to(ROOT/'eReader_cards')),
                              'sha256':sha256(raw),'id':header&511,'type':header>>9,
                              'applicability':decoded[PAYLOAD+(11 if group_id==0 else 9)]})
                included.add(path.resolve())
        groups.append({'title':title,'folder':folders[0],'source_folders':list(folders),
                       'kind':'power' if group_id==0 else 'demo','context':group_id,'cards':cards})
    all_bins={p.resolve() for p in (ROOT/'eReader_cards').rglob('*') if p.is_file() and p.suffix.lower()=='.bin'}
    if all_bins!=included:raise ValueError(f'Uncategorized BIN files: {sorted(str(p) for p in all_bins-included)}')
    world_power=deepcopy(groups[0])
    world_power.update(title='POWER-UP CARDS - WORLD-E',context=2)
    groups[0]['context']=1
    groups.append(world_power)
    return prepare_catalog(groups)


def decoded_card(raw):
    if len(raw) == 0x840 and raw[0x1A:0x22] == b'NINTENDO': return raw
    if len(raw) != 0xB60: raise ValueError('Unsupported card envelope')
    # NEDC long strip: discard two header bytes per 104-byte block, then
    # deinterleave 44 RS codewords, retaining each codeword's 48 data bytes.
    # Source hashes are recorded and rechecked; these supplied strips are intact.
    stream = b''.join(raw[i+2:i+104] for i in range(0,len(raw),104))[:64*44]
    decoded = bytes(stream[row*44+col] for col in range(44) for row in range(48))
    if decoded[0x1A:0x22] != b'NINTENDO': raise ValueError('Invalid raw card envelope')
    return decoded


GROUP_CATALOG=discover_catalog()


def build(rom, output, compiler, catalog=None):
    original = rom.read_bytes()
    if sha256(original) != ROM_SHA256: raise ValueError('Expected original USA Rev 1 VC ROM')
    if ROOT/'original_rom' in output.parents or ROOT/'eReader_cards' in output.parents or output==rom:
        raise ValueError('Output must not overwrite source ROM/cards')
    if any(original[CODE:]): raise ValueError('Reserved injection area is occupied')
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
    cursor=ASSETS
    def allocate(data):
        nonlocal cursor
        cursor=(cursor+3)&~3
        address=cursor
        if cursor+len(data)>0x1000000:raise ValueError('Menu assets exceed 16 MiB ROM budget')
        if cursor+len(data)>len(patched):patched.extend(bytes(0x1000000-len(patched)))
        put(cursor,data)
        cursor+=len(data)
        return BASE+address
    groups=[]
    cache={}
    for group_id,definition in enumerate(GROUP_CATALOG if catalog is None else prepare_catalog(catalog)):
        cards=[]
        descriptor=bytearray()
        for spec in definition['cards']:
            path=ROOT/'eReader_cards'/spec['folder']/spec['file']
            raw=path.read_bytes()
            if sha256(raw)!=spec['sha256']: raise ValueError(f'Unexpected card: {path}')
            decoded=decoded_card(raw)
            header=int.from_bytes(decoded[PAYLOAD:PAYLOAD+2],'little')
            is_demo=definition['kind']=='demo'
            if header&511!=spec['id'] or header>>9 not in ((4,) if is_demo else (2,3)):
                raise ValueError('Unexpected game header')
            applicability=decoded[PAYLOAD+(9 if is_demo else 11)]
            if applicability not in (0,definition['context']) and spec['folder']!='Switch':
                raise ValueError('Card applicability does not match catalog')
            if path not in cache:
                raw_address=allocate(raw)
                canonical=raw_address if raw==decoded else allocate(decoded)
                transfer=allocate(decoded[PAYLOAD:].ljust(0x80C,b'\0')) if is_demo else 0
                cache[path]=(raw_address,canonical,transfer)
            raw_address,canonical,transfer=cache[path]
            descriptor+=struct.pack('<II',canonical+PAYLOAD,transfer)
            cards.append({**spec,'path':str(path),'bin_rom_offset':hex(raw_address-BASE),
                          'decoded_sha256':sha256(decoded),'payload_address':hex(canonical+PAYLOAD),
                          'transfer_address':hex(transfer) if transfer else None})
        groups.append({**definition,'cards':cards,'descriptor_address':allocate(descriptor)})
    pages,palette,status_glyphs,style_info=make_assets(groups,original)
    palette_address=allocate(palette)
    status_address=allocate(status_glyphs)
    for group_id,(group,group_pages) in enumerate(zip(groups,pages)):
        page_descriptors=bytearray()
        for page in group_pages:
            page_descriptors+=struct.pack('<IIIII',allocate(page['font']),allocate(page['maps']),allocate(page['arrows']),allocate(page['status_base']),allocate(page['confirmation_fonts']))
        pages_address=allocate(page_descriptors)
        group['pages_address']=hex(pages_address)
        group['page_count']=len(group_pages)
        put(GROUPS+group_id*12,struct.pack('<III',group['descriptor_address'],len(group['cards']),pages_address))
    (work/'menu_assets.h').write_text(f'#define MENU_PALETTE_ADDRESS 0x{palette_address:08X}\n#define MENU_GROUPS_ADDRESS 0x{BASE+GROUPS:08X}\n#define MENU_STATUS_ADDRESS 0x{status_address:08X}\n')
    objects=[]
    for name in ('card_menu','card_menu_view'):
        obj=work/(name+'.o')
        subprocess.run([compiler,'--target=arm-none-eabi','-mcpu=arm7tdmi','-mthumb','-Os',
                        '-ffreestanding','-fno-builtin','-fno-jump-tables','-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables','-Wall','-Wextra','-Werror','-I',str(work),
                        '-c',str(ROOT/'scripts'/(name+'.c')),'-o',str(obj)],check=True)
        objects.append(obj.read_bytes())
    code,symbols=link(objects,BASE+CODE)
    if len(code)>GROUPS-CODE: raise ValueError('Code exceeds reserved region')
    put(CODE,code)
    if original[HOOK:HOOK+4]!=struct.pack('<I',0x080C1039):raise ValueError('Unexpected dispatch table')
    put(HOOK,struct.pack('<I',symbols['card_menu_tick']))
    scene_pointer=struct.pack('<I',0x080C4AC9)
    scene_hooks=[i for i in range(0,len(original)-3,4) if original[i:i+4]==scene_pointer]
    if len(scene_hooks)!=1:raise ValueError('Expected one native communication-state dispatch pointer')
    put(scene_hooks[0],struct.pack('<I',symbols['menu_scene_tick']))
    # Native category sign text/frame use this shared Y coordinate on the entire
    # communication screen, including its initial OK and subsequent dialogs.
    if original[0xC48FC:0xC48FE]!=struct.pack('<H',0x2004):raise ValueError('Unexpected native category-banner Y coordinate')
    put(0xC48FC,struct.pack('<H',0x2001))
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
            'source_card_count':len(cache),'menu_entry_count':sum(len(g['cards']) for g in groups),
            'compiler':subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    assert rom.read_bytes()==original
    print(json.dumps({'output':str(output),'sha256':report['output_sha256'],'code_size':len(code),'cards':len(cache)},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,default=ROOT/'original_rom'/ROM_NAME)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--compiler',default=shutil.which('clang'))
    p.add_argument('--paging-test',action='store_true',help='Build the earlier separate 18-row paging fixture from the first two cards per group')
    args=p.parse_args()
    if not args.compiler:p.error('Clang is required; pass --compiler PATH')
    catalog=None
    if args.paging_test:
        if args.output==DEFAULT_OUTPUT:args.output=ROOT/'output/sma4-card-menu-paging-test.gba'
        catalog=deepcopy(GROUP_CATALOG)
        for group in catalog:group['cards']=group['cards'][:2]*9
    build(args.rom.resolve(),args.output.resolve(),args.compiler,catalog)


if __name__=='__main__':main()
