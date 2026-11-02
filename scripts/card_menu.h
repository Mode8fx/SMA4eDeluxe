#ifndef CARD_MENU_H
#define CARD_MENU_H
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define MEM8(a) (*(volatile u8 *)(a))
#define MEM16(a) (*(volatile u16 *)(a))
#define DISPLAY_BUFFER MEM16(0x03002D84)
#define BUTTONS_HELD MEM16(0x03002DBC)
#define BUTTONS_PRESSED MEM16(0x03002DBE)

typedef struct { const u8 *payload; const u8 *transfer; } Card;
typedef struct { const Card *cards; u32 count; const u16 *maps; const void *font; } CardGroup;
typedef struct {
    u8 active, selected, armed, group;
    u16 bg1_control, bg1_x, bg1_y, bg1_enabled;
    u8 confirming, choice;
    u16 sprites_enabled;
} MenuState;
/* Appended to the native communication object; native allocation/clear are extended. */
#define MENU_STATE(screen) ((MenuState *)((u8 *)(screen) + 0x1F4))
#define SCREEN_STATE(screen) MEM8((u8 *)(screen) + 0x1E7)
#define SCREEN_TIMER(screen) MEM16((u8 *)(screen) + 0x1CE)
#define SCREEN_INPUT_TIMER(screen) MEM16((u8 *)(screen) + 0x1D0)

static inline void copy_words(const void *source, void *dest, u32 words) {
    ((void (*)(const void *, void *, u32))0x080E7591)(source, dest, words);
}
void menu_view_open(MenuState *state, const CardGroup *group);
void menu_view_update(MenuState *state, const CardGroup *group);
void menu_view_close(MenuState *state);
#endif
