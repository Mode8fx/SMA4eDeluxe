"""World-e list appearance, using the original ROM's variable-width font.

This module controls pixels only. Filtering, selection and confirmation behavior
remain in the shared C controller.
"""
import struct

MAX_NAME_LENGTH = 32
DESCRIPTION_COLOR = 9
CARD_DESCRIPTIONS = {
    '4-Item Set': 'One of each standard item.',
    '3-Suit Set': 'One of each suit.',
    '8-Item Set': '4-Item + 3-Suit + P-Wing',
    '10-Item Set': '8-Item + Cape Feather + Boomerang',
    'Blue Switch': '1-Up Mushrooms turn into 3-Up Moons.',
    'Orange Switch': 'Fireballs turn enemies into coins.',
    'Blue-Green Switch': '(Main game only) Adds grass and turnips to levels.',
    'Yellow Switch': '(Main game only) Gives Luigi different physics and fireballs.',
    'Green Switch': '(World-e only) Slows down time limit and music early in a level.',
    'Cyan Switch': '(World-e only) Cuts P-Meter in half.',
    'Red Switch': 'Harder enemies, more damage, 2x boss health, 2x score.',
}


def description_lines(card, font):
    if card['folder'] not in ('Power-Up', 'Switch'): return []
    # The source filename spells this colour with a space.
    name = card['name'].replace('Blue Green Switch', 'Blue-Green Switch')
    text = CARD_DESCRIPTIONS.get(name, '')
    lines = []
    for word in text.split():
        candidate = f'{lines[-1]} {word}' if lines else word
        if lines and len(candidate) <= 35 and font.width(candidate) <= 176:
            lines[-1] = candidate
        else:
            if len(word) > 35 or font.width(word) > 176:
                raise ValueError(f'Description word does not fit: {word}')
            lines.append(word)
    if len(lines) > 2: raise ValueError(f'Description exceeds two lines: {name}')
    return lines


def lz77(rom, offset):
    if rom[offset] != 0x10:
        raise ValueError('Expected GBA LZ77 graphics')
    size = int.from_bytes(rom[offset+1:offset+4], 'little')
    result = bytearray()
    cursor = offset+4
    while len(result) < size:
        flags = rom[cursor]
        cursor += 1
        for bit in range(7,-1,-1):
            if len(result) >= size: break
            if flags & (1 << bit):
                high,low = rom[cursor:cursor+2]
                cursor += 2
                distance = ((high & 15) << 8 | low)+1
                for _ in range((high >> 4)+3): result.append(result[-distance])
            else:
                result.append(rom[cursor])
                cursor += 1
    return bytes(result[:size])


class NativeFont:
    def __init__(self, rom):
        self.graphics = lz77(rom, 0x23ABFC)
        self.offsets = struct.unpack_from('<256H',rom,0x2352E4)
        self.widths = rom[0x23550E:0x23560E]

    def glyph(self, char, small=False):
        if 'A' <= char <= 'Z': code = ord(char)-ord('A')
        elif 'a' <= char <= 'z': code = ord(char)-ord('a')+0x20
        elif '0' <= char <= '9': code = ord(char)-ord('0')+(0xEE if small else 0x76)
        elif char in " -?'.,": code = {' ':0xE3,'-':0xE2,'?':0xE0,"'":0x1C,'.':0x1E,',':0x1D}[char]
        elif char in '()+':
            # These symbols have no glyph in this native font. Match its pixel
            # weight and eight-pixel height without substituting another font.
            rows = {'(':('0110','1100','1100','1100','1100','1100','0110','0000'),
                    ')':('1100','0110','0110','0110','0110','0110','1100','0000'),
                    '+':('000000','001100','001100','111111','111111','001100','001100','000000')}[char]
            return len(rows[0]), [[int(pixel) for pixel in row] for row in rows]
        elif char == ':':
            # Two copies of the native full stop's 2-pixel dot.
            return 3, [[int(x<2 and y in (2,3,5,6)) for x in range(3)] for y in range(8)]
        elif char == '/':
            return 4, [[int(x == 3-y//2) for x in range(4)] for y in range(8)]
        else: raise ValueError(f'Unsupported native-font character: {char!r}')
        width = self.widths[code]
        offset = self.offsets[code]
        pixels = [[(self.graphics[offset+(x//8)*32+y*4+(x%8)//2] >> (4*(x%2))) & 15
                   for x in range(width)] for y in range(8)]
        return width,pixels

    def width(self, text, small=False):
        return sum(self.glyph(char,small)[0] for char in text)

    def draw(self, canvas, x, y, text, color=2, small=False):
        for char in text:
            width,pixels = self.glyph(char,small)
            for row in range(8):
                for column in range(width):
                    if pixels[row][column]: canvas[y+row][x+column] = color
            x += width


def rgb(r,g,b):
    return (r>>3) | (g>>3)<<5 | (b>>3)<<10


def cursor(canvas,x,y):
    # Native-style outlined right-pointing triangle.
    for row in range(9):
        half = min(row,8-row)
        for col in range(half+1):
            canvas[y+row][x+col] = 1 if col==0 or col==half else 2


# Palette indices 1/2/3/13/14/15 retain the native frame's meanings.
# These are its displayed BGR555 colours, including the game's palette adjustment.
COLORS = [0,0x7FFF,0,0x6F7B,0x1A5F,0x233F,0x27DF,0x001F,
          0x030E,rgb(0,64,224),0x4210,rgb(0,160,0),rgb(216,0,0),0x6DE5,0x7F72,0x7FB5]


def native_palette(rom):
    """Unadjusted colours for the same native conversion as the Level Card UI."""
    frame=struct.unpack_from('<16H',rom,0x23CF68+12*32)
    arrow=struct.unpack_from('<16H',rom,0x23BF10+13*32)
    result=[0]*16
    for index in (1,2,3,13,14,15):result[index]=frame[index]
    for index in range(4,9):result[index]=arrow[index-1]
    # Custom text colours were authored as displayed GBA colours. Find exact
    # source components so normal GBA mode retains its existing appearance.
    gba=struct.unpack_from('<32H',rom,0x121E3C+64)
    for index in range(9,13):
        result[index]=sum(gba.index((COLORS[index]>>shift)&31)<<shift for shift in (0,5,10))
    assert [sum(gba[(color>>shift)&31]<<shift for shift in (0,5,10))
            for color in result]==COLORS
    return result


def tile_pixel(graphics, tile, x, y):
    return (graphics[tile*32+y*4+x//2] >> (4*(x%2))) & 15


class NativeFrame:
    def __init__(self, rom):
        self.graphics = lz77(rom,0x235C08)
        self.map = struct.unpack('<2048H',lz77(rom,0x23D560))
        self.sprites = lz77(rom,0x239B70)
        self.dialogue = lz77(rom,0x23F48C)

    def canvas(self):
        # Preserve the complete native frame; page_indicator replaces its count.
        canvas = [[0]*256 for _ in range(256)]
        for y in range(160):
            for x in range(240):
                cell = self.map[(y//8)*32+x//8]
                if cell == 0x3FF: continue
                xx = 7-x%8 if cell&0x400 else x%8
                yy = 7-y%8 if cell&0x800 else y%8
                canvas[y][x] = tile_pixel(self.graphics,cell&1023,xx,yy)
        return canvas

    def page_indicator(self, canvas, page, pages):
        # The native footer uses dedicated eight-pixel BG tiles, rather than
        # the variable-width text font. Keep its blue ink and background pixels.
        text=f'{page}/{pages}'
        left=224-len(text)*8
        for index,char in enumerate(text):
            tile=0x246 if char=='/' else 0x236+int(char)
            for y in range(8):
                for x in range(8):
                    canvas[112+y][left+index*8+x]=tile_pixel(self.graphics,tile,x,y)

    def sprite(self, canvas, x, y, tile, height=8, flip=False):
        # Exact original cursor / page-arrow sprite, mapped into our BG palette.
        remap = {1:1,2:2,3:4,4:5,5:6,6:7,7:8}
        for row in range(height):
            for col in range(8):
                pixel = tile_pixel(self.sprites,tile+(row//8)*32,
                                   7-col if flip else col,row%8)
                if pixel:canvas[y+row][x+col] = remap[pixel]

    def communication_box(self, canvas, x0, y0, x1, y1):
        # Nine-slice the original communication window's white/black frame.
        # Its corner, horizontal edge and vertical edge are native OBJ tiles.
        for y in range(y0,y1):
            for x in range(x0,x1):
                dx=min(x-x0,x1-1-x)
                dy=min(y-y0,y1-1-y)
                if dx<8 and dy<8:tile,tx,ty=0x390,dx,dy
                elif dy<8:tile,tx,ty=0x391,dx%8,dy
                elif dx<8:tile,tx,ty=0x3B0,dx,dy%8
                else:tile,tx,ty=0x3B1,0,0
                pixel=tile_pixel(self.dialogue,tile,tx,ty)
                if pixel:canvas[y][x]=2 if pixel==1 else 1


def list_canvas(group, selected, font, frame):
    canvas = frame.canvas()
    page = selected//8
    for row,card in enumerate(group['cards'][page*8:page*8+8]):
        top = 25+row*11
        number = str(card['menu_id'])
        font.draw(canvas,39-font.width(number,True),top,number,small=True)
        # Real catalog names fit in this field with the original glyph spacing.
        # For unusually wide names remove only glyphs' trailing blank columns
        # as needed, preserving the actual native glyph pixels.
        name = card['name']
        excess = max(0,font.width(name)-158)
        x = 45
        for i,char in enumerate(name):
            width,pixels=font.glyph(char)
            for y in range(8):
                for xx in range(width):
                    if pixels[y][xx] and x+xx<203:canvas[top+y][x+xx]=2
            squeeze = min(excess,1) if i+1<len(name) else 0
            excess -= squeeze
            x += width-squeeze
        font.draw(canvas,204,top,card['region'])
    frame.sprite(canvas,16,24+(selected%8)*11,0x5D)
    frame.page_indicator(canvas,page+1,(len(group['cards'])+7)//8)
    return canvas


def confirmation_bounds(card, font):
    lines = description_lines(card, font)
    top = 44 - (11*len(lines))//2
    return top, top+60+11*len(lines)


def confirmation(canvas, choice, font, frame, card):
    lines = description_lines(card, font)
    top,bottom = confirmation_bounds(card,font)
    frame.communication_box(canvas,24,top,216,bottom)
    prompt='Scan this card?'
    font.draw(canvas,(240-font.width(prompt))//2,top+9,prompt)
    font.draw(canvas,(240-font.width(card['name']))//2,top+22,card['name'])
    for i,line in enumerate(lines):
        font.draw(canvas,(240-font.width(line))//2,top+35+11*i,line,color=DESCRIPTION_COLOR)
    buttons = top+41+11*len(lines)
    font.draw(canvas,78,buttons,'Yes',color=2 if choice==0 else 10)
    font.draw(canvas,143,buttons,'No',color=2 if choice==1 else 10)
    cursor(canvas,65 if choice==0 else 130,buttons-1)


def make_assets(groups, rom):
    font=NativeFont(rom)
    frame=NativeFrame(rom)
    pages=[]
    metrics=[]
    popup_metrics=[]
    status_glyphs=bytearray()
    for label,color in (('ON',11),('OFF',12),('N/A',2)):
        canvas=[[0]*24 for _ in range(8)]
        font.draw(canvas,24-font.width(label),0,label,color=color)
        status_glyphs.extend(canvas[y][x] | canvas[y][x+1]<<4
                             for y in range(8) for x in range(0,24,2))
    for group in groups:
        group_pages=[]
        for first in range(0,len(group['cards']),8):
            tiles=[bytes(32)]
            tile_ids={tiles[0]:0}
            def cell(canvas,tx,ty,tiles=tiles,tile_ids=tile_ids):
                # Every view refers to the persistent status strip, including
                # confirmations. Cursor changes never replace it with blank tiles.
                if group['kind']=='power' and 22<=tx<25 and 3<=ty<14:
                    return 0xE200+(ty-3)*3+tx-22
                tile=bytes(canvas[ty*8+y][tx*8+x] | canvas[ty*8+y][tx*8+x+1]<<4
                           for y in range(8) for x in range(0,8,2))
                if tile not in tile_ids:
                    tile_ids[tile]=len(tiles)
                    tiles.append(tile)
                return tile_ids[tile] | 0xE000
            maps=[]
            confirmation_fonts=[]
            confirmation_arrows=[]
            def status_record(canvas,top=0,bottom=0):
                return struct.pack('<BBH',top,bottom,0)+bytes(
                    canvas[ty*8+y][tx*8+x] | canvas[ty*8+y][tx*8+x+1]<<4
                    for ty in range(3,14) for tx in range(22,25)
                    for y in range(8) for x in range(0,8,2))
            status_bases=[status_record(list_canvas(group,first,font,frame))]
            for selected in range(first,min(first+8,len(group['cards']))):
                popup_tiles=[bytes(32)]
                popup_ids={popup_tiles[0]:0}
                for mode in range(3):
                    canvas=list_canvas(group,selected,font,frame)
                    if len(group['cards'])>8:frame.sprite(canvas,224,65,0x1D,16,True)
                    if mode:confirmation(canvas,mode-1,font,frame,group['cards'][selected])
                    if mode:
                        record=status_record(canvas,*confirmation_bounds(group['cards'][selected],font))
                        if mode==1:status_bases.append(record)
                        elif record!=status_bases[-1]:raise ValueError('Yes/No must share the status-strip background')
                    maps.append(struct.pack('<1024H',*((cell(canvas,x,y,popup_tiles,popup_ids)
                                if mode else cell(canvas,x,y))
                                for y in range(32) for x in range(32))))
                for x in range(223,227):
                    canvas=list_canvas(group,selected,font,frame)
                    confirmation(canvas,0,font,frame,group['cards'][selected])
                    if len(group['cards'])>8:frame.sprite(canvas,x,65,0x1D,16,True)
                    confirmation_arrows.extend(cell(canvas,tx,ty,popup_tiles,popup_ids)
                                               for ty in range(8,10) for tx in range(27,30))
                if len(popup_tiles)>512:raise ValueError(f'Confirmation exceeds 512 tiles: {len(popup_tiles)}')
                popup_metrics.append(len(popup_tiles))
                confirmation_fonts.append(b''.join(popup_tiles).ljust(0x4000,b'\0'))
            arrows=[]
            for x in range(223,227):
                canvas=list_canvas(group,first,font,frame)
                if len(group['cards'])>8:frame.sprite(canvas,x,65,0x1D,16,True)
                arrows.extend(cell(canvas,tx,ty) for ty in range(8,10) for tx in range(27,30))
            graphics=b''.join(tiles)
            if len(graphics)>0x4000:raise ValueError(f'Menu page exceeds 512 tiles: {len(tiles)}')
            group_pages.append({'font':graphics.ljust(0x4000,b'\0'),
                                'confirmation_fonts':b''.join(confirmation_fonts),
                                'maps':b''.join(maps),'arrows':struct.pack(f'<{len(arrows)+len(confirmation_arrows)}H',*arrows,*confirmation_arrows),
                                'status_base':b''.join(status_bases)})
            metrics.append(len(tiles))
        pages.append(group_pages)
    return pages,struct.pack('<16H',*native_palette(rom)),bytes(status_glyphs),{
        'font':'Original World-e variable-width font','font_rom_offset':'0x23abfc',
        'frame_graphics_rom_offset':'0x235c08','frame_map_rom_offset':'0x23d560',
        'arrow_graphics_rom_offset':'0x239b70','arrow_cycle_frames':22,
        'page_indicator':{'graphics_rom_offset':'0x235c08','digit_tiles':'0x236..0x23f',
                          'slash_tile':'0x246','tile_y':112,'right_edge':224,
                          'color_index':13,'native_gba_gbp_colors':True},
        'confirmation_frame_graphics_rom_offset':'0x23f48c',
        'confirmation_includes_card_name':True,
        'confirmation_graphics':'Separate 16 KiB bank per card, shared by Yes/No',
        'confirmation_tiles_per_card':popup_metrics,
        'confirmation_descriptions':{'color':'blue','max_line_length':35,'max_lines':2,
                                    'text':CARD_DESCRIPTIONS},
        'tiles_per_page':metrics,'rows':8,'variants_per_card':3,
        'columns':['filename ID',f'filename name (up to {MAX_NAME_LENGTH} characters)','region (JPN/USA, black)'],
        'max_name_length':MAX_NAME_LENGTH,
        'palette':{'conversion':'Native AdjustPalComponents (BG/custom table 0, arrow table 1)',
                   'routine_address':'0x080079cd','mode_address':'0x0300378b',
                   'frame_source_offset':'0x23d0e8','arrow_source_offset':'0x23c0b0',
                   'gba_colors_preserved':True,'follows_gbp_mode':True},
        'switch_status':{'ON':'green','OFF':'red','N/A':'black','field_x':176,'field_right':200,
                         'persistent_map_tiles':True,'confirmation_occlusion':True,
                         'status_background_record_bytes':1060,'updates_during_vblank':True},
        'category_banner_y_offset':-3,
        'native_category_banner_preserved':True,'confirmation':'Scan this card?'}
