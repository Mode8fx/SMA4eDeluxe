/* One controller and one activation path for Power-Up, normal Demo and World-e Demo. */
#include "card_menu.h"
#include "menu_assets.h"

static void sound(u32 id) {
    ((void (*)(u32, void *))0x080E4861)(id, (void *)0x03002D7C);
}
static void window(void *object, u32 x, u32 y, u32 kind, u32 message) {
    ((void (*)(void *, u32, u32, u32, u32))0x080C4BC1)(object,x,y,kind,message);
}
static void show_ready(void *screen) {
    window(((void **)screen)[1],120,32,2,7);
    SCREEN_TIMER(screen) = 0;
    SCREEN_INPUT_TIMER(screen) = 0;
    SCREEN_STATE(screen) = 2;
}
static void activate(void *screen, const Card *card) {
    const u8 *payload = card->payload;
    if (card->transfer) {
        copy_words(card->transfer, (void *)0x02004000, 0x200);
        copy_words(card->transfer + 12, (void *)0x020143D0, 0x200);
        payload = (const u8 *)0x02004000;
    }
    ((void (*)(const u8 *))0x080E3099)(payload);
    MEM8(0x0300376C) = 12;
    *(volatile u32 *)0x030079D8 = 2;
    sound(6);
    window(((void **)screen)[0],120,80,0,4);
    MEM8((u8 *)((void **)screen)[0] + 10) = 11;
    SCREEN_TIMER(screen) = 0;
    SCREEN_STATE(screen) = 6;
}

__attribute__((section(".text.entry")))
void card_menu_tick(void *screen) {
    u8 category = MEM8(0x0300378B) & 0xF0;
    MenuState *state = MENU_STATE(screen);
    const CardGroup *groups = (const CardGroup *)MENU_GROUPS_ADDRESS;
    const CardGroup *group;
    u32 selected;
    if (category != 0x10 && category != 0x20) {
        ((void (*)(void *))0x080C1039)(screen);
        return;
    }
    if (!state->active) {
        if (MEM8((u8 *)((void **)screen)[1] + 8) != 0) return;
        state->group = category == 0x20 ? 0 : ((MEM8(0x03003DCF) & 0x40) ? 1 : 2);
        state->selected = 0;
        state->armed = 0;
        state->active = 1;
        state->confirming = 0;
        state->choice = 0;
        menu_view_open(state, &groups[state->group]);
        return; /* Opening OK can never activate a card. */
    }
    group = &groups[state->group];
    if (!state->armed) {
        if (!(BUTTONS_HELD & 0xF3)) state->armed = 1;
        return;
    }
    if (state->confirming) {
        u8 choice = state->choice;
        if (BUTTONS_PRESSED & 0x20) choice = 0; /* Left: Yes. */
        else if (BUTTONS_PRESSED & 0x10) choice = 1; /* Right: No. */
        if (choice != state->choice) {
            state->choice = choice;
            sound(0x45);
            menu_view_update(state, group);
        }
        if ((BUTTONS_PRESSED & 2) || ((BUTTONS_PRESSED & 1) && state->choice)) {
            state->confirming = 0;
            state->armed = 0;
            sound(14);
            menu_view_update(state, group);
        } else if ((BUTTONS_PRESSED & 1) && !state->choice) {
            const Card *card = &group->cards[state->selected];
            menu_view_close(state);
            activate(screen, card);
        }
        return;
    }
    if (BUTTONS_PRESSED & 2) {
        menu_view_close(state);
        sound(14);
        show_ready(screen);
        return;
    }
    selected = state->selected;
    if (BUTTONS_PRESSED & 0x40) selected = selected ? selected - 1 : group->count - 1;
    else if (BUTTONS_PRESSED & 0x80) selected = selected + 1 < group->count ? selected + 1 : 0;
    if (selected != state->selected) {
        state->selected = selected;
        sound(0x45);
        menu_view_update(state, group);
    }
    if (BUTTONS_PRESSED & 1) {
        state->confirming = 1;
        state->choice = 0;
        state->armed = 0;
        sound(5);
        menu_view_update(state, group);
    }
}
