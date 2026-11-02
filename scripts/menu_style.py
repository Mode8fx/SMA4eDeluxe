"""World-e list appearance, using the original ROM's variable-width font.

This module controls pixels only. Filtering, selection and confirmation behavior
remain in the shared C controller.
"""
import struct


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
        elif char in ' -?': code = {' ':0xE3,'-':0xE2,'?':0xE0}[char]
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


def rectangle(canvas,x0,y0,x1,y1,color):
    for y in range(y0,y1):
        for x in range(x0,x1):canvas[y][x] = color


def rounded(canvas,x0,y0,x1,y1,color,radius=2):
    rectangle(canvas,x0+radius,y0,x1-radius,y1,color)
    rectangle(canvas,x0,y0+radius,x1,y1-radius,color)
    if radius>1:
        rectangle(canvas,x0+1,y0+1,x1-1,y1-1,color)


def cursor(canvas,x,y):
    # Native-style outlined right-pointing triangle.
    for row in range(9):
        half = min(row,8-row)
        for col in range(half+1):
            canvas[y+row][x+col] = 7 if col==0 or col==half else 2


def list_canvas(group, selected, font):
    canvas = [[0]*256 for _ in range(256)]
    # The native scan-screen sprites are hidden while this overlay is active.
    # Recreate its small blue category sign using the same World-e font.
    title='Power-Up Card' if group['folder']=='Power-Up' else 'Demo Card'
    rounded(canvas,5,3,105,18,7,2)
    rectangle(canvas,6,4,104,17,2)
    rectangle(canvas,7,5,103,16,10)
    title_x=(110-font.width(title))//2
    font.draw(canvas,title_x+1,7,title,color=2)
    font.draw(canvas,title_x,6,title,color=7)
    rounded(canvas,5,21,237,130,5,3)
    rounded(canvas,3,19,236,129,7,3)
    rounded(canvas,4,20,235,128,5,2)
    rounded(canvas,5,21,234,127,6,2)
    rectangle(canvas,7,23,232,125,1)
    # Eight compact rows and a footer, matching World-e's level-list proportions.
    for row in range(8):
        top = 24+row*11
        if row==selected:rectangle(canvas,8,top,232,top+10,3)
        for x in range(15,229,2):canvas[top+10][x] = 8
        if row < len(group['cards']):
            card = group['cards'][row]
            number = str(card['menu_id'])
            font.draw(canvas,39-font.width(number,True),top+1,number,small=True)
            font.draw(canvas,45,top+1,card['name'])
            font.draw(canvas,214,top+1,card['region'],color=11 if card['region']=='J' else 12)
    cursor(canvas,11,24+selected*11)
    rectangle(canvas,7,113,232,125,1)
    font.draw(canvas,16,116,'A Select   B Back',color=2)
    font.draw(canvas,15,115,'A Select   B Back',color=7)
    count=f'{selected+1}/{len(group["cards"])}'
    count_x=224-font.width(count)
    font.draw(canvas,count_x+1,116,count,color=2)
    font.draw(canvas,count_x,115,count,color=7)
    return canvas


def confirmation(canvas, choice, font):
    rounded(canvas,49,48,193,105,5,2)
    rounded(canvas,47,46,192,104,7,3)
    rounded(canvas,48,47,191,103,2,2)
    rectangle(canvas,50,49,189,101,4)
    prompt='Scan this card?'
    font.draw(canvas,(240-font.width(prompt))//2,61,prompt)
    font.draw(canvas,78,85,'Yes',color=2 if choice==0 else 9)
    font.draw(canvas,143,85,'No',color=2 if choice==1 else 9)
    cursor(canvas,65 if choice==0 else 130,84)


def make_assets(groups, rom):
    font=NativeFont(rom)
    colors=[0,rgb(145,218,239),0,rgb(222,247,247),rgb(255,255,239),rgb(82,49,8),
            rgb(255,239,181),rgb(255,255,255),rgb(75,167,196),rgb(145,145,145),
            rgb(65,135,185),rgb(224,80,48),rgb(16,151,80),0,0,0]
    palette=struct.pack('<16H',*colors)
    banks=[]
    tile_counts=[]
    maps=[]
    for group in groups:
        if len(group['cards'])>8:raise ValueError('Current view supports eight visible rows; add scrolling before expanding it')
        for selected in range(len(group['cards'])):
            for mode in range(3):
                tiles=[bytes(32)]
                tile_ids={tiles[0]:0}
                canvas=list_canvas(group,selected,font)
                if mode:confirmation(canvas,mode-1,font)
                cells=[]
                for ty in range(32):
                    for tx in range(32):
                        tile=bytes(canvas[ty*8+y][tx*8+x] | canvas[ty*8+y][tx*8+x+1]<<4
                                   for y in range(8) for x in range(0,8,2))
                        if tile not in tile_ids:
                            tile_ids[tile]=len(tiles)
                            tiles.append(tile)
                        cells.append(tile_ids[tile] | 0xE000)
                maps.append(struct.pack('<1024H',*cells))
                graphics=b''.join(tiles)
                if len(graphics)>0x2000:raise ValueError(f'Menu graphics exceed 256 tiles: {len(tiles)}')
                banks.append(graphics.ljust(0x2000,b'\0'))
                tile_counts.append(len(tiles))
    return b''.join(banks),palette,maps,{
        'font':'World-e native variable-width font','font_rom_offset':'0x23abfc',
        'tiles_per_view':tile_counts,'rows':8,'variants_per_card':3,
        'columns':['ID (1..100)','name (1..20 characters)','region (J/U)'],
        'confirmation':'Scan this card?'}
