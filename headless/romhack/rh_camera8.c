
#include <stdint.h>

typedef void (*CamFn)(void *);

typedef struct {
    char **gPlayer1Controller;
    int64_t ctlDownOff, ctlPressedOff;
    int16_t *s8DirModeYawOffset;
    int16_t *sSelectionFlags;
    char **sMarioCamState;
    int64_t camStateYawOff;
    char *gMarioStates;
    int64_t msYawOff;
    CamFn fn[15];
    int32_t *toggle;
    uint32_t *last;
} Globals;

enum { RH8_BEHIND, RH8_CUP, RH8_WATER, RH8_CANNON, RH8_8DIR, RH8_RADIAL, RH8_OUTWARD, RH8_LAKITU, RH8_BOSS,
       RH8_PARALLEL, RH8_SLIDE, RH8_FIXED, RH8_SPIRAL, RH8_MARIO, RH8_RESET };

static const uint32_t CASE[] = {0x80286C84, 0x80286C94, 0x80286CA4, 0x80286CB4, 0x80286CC4, 0x80286CD4, 0x80286CE4,
                                0x80286CF4, 0x80286D14, 0x80286D24, 0x80286D34, 0x80286D44, 0x80286D54};

static Globals G;

#define TOGGLE(c) (*G.toggle)
#define LAST(c) (*G.last)

__declspec(dllexport) void rh_cam8_init(const Globals *g)
{
    G = *g;
}

static uint16_t ctl(int64_t off) { return *(uint16_t *) (*G.gPlayer1Controller + off); }
static int16_t cam_yaw(void) { return *(int16_t *) (*G.sMarioCamState + G.camStateYawOff); }

static void run(uint32_t c9, void *c)
{
    for (int i = 0; i <= RH8_SPIRAL; i++) {
        if (CASE[i] == c9) {
            G.fn[i](c);
            return;
        }
    }
}

static void dispatch(void *c, int which)
{
    uint32_t t9 = CASE[which];
    if (t9 > 0x80286CB4)
        t9 = 0x80286CC4;
    if (ctl(G.ctlPressedOff) & 0x0020)
        TOGGLE(c) = 1 - TOGGLE(c);
    if (TOGGLE(c) && (t9 == 0x80286C84 || t9 == 0x80286CA4))
        t9 = 0x80286CC4;
    int32_t a0 = *G.s8DirModeYawOffset;
    uint16_t down = ctl(G.ctlDownOff);
    if (down & 0x0200)
        a0 -= 0x40;
    else if (down & 0x0100)
        a0 += 0x40;
    else if (down & 0x0800)
        a0 = cam_yaw() - 0x8000;
    else if (ctl(G.ctlPressedOff) & 0x0400)
        a0 = (a0 + 0x1000) & 0xE000;
    *G.s8DirModeYawOffset = (int16_t) a0;
    if (LAST(c) != t9) {
        *G.s8DirModeYawOffset = (int16_t) ((cam_yaw() - 0x7000) & 0xE000);
        LAST(c) = t9;
    }
    run(t9, c);
}


static void call(void *c, int which)
{
    if (*G.sSelectionFlags & 1) {
        if (!TOGGLE(c))
            LAST(c) = 0;
        G.fn[which](c);
    } else {
        dispatch(c, which);
    }
}

#define WRAP(name, idx) __declspec(dllexport) void rh_cam8_##name(void *c) { call(c, idx); }
WRAP(behind, RH8_BEHIND)
WRAP(cup, RH8_CUP)
WRAP(water, RH8_WATER)
WRAP(cannon, RH8_CANNON)
WRAP(eightdir, RH8_8DIR)
WRAP(radial, RH8_RADIAL)
WRAP(outward, RH8_OUTWARD)
WRAP(lakitu, RH8_LAKITU)
WRAP(boss, RH8_BOSS)
WRAP(parallel, RH8_PARALLEL)
WRAP(slide, RH8_SLIDE)
WRAP(fixed, RH8_FIXED)
WRAP(spiral, RH8_SPIRAL)


__declspec(dllexport) void rh_cam8_mario(void *c)
{
    if (!TOGGLE(c))
        LAST(c) = 0;
    G.fn[RH8_MARIO](c);
}

__declspec(dllexport) void rh_cam8_reset(void *c)
{
    G.fn[RH8_RESET](c);
    *G.s8DirModeYawOffset = (int16_t) ((*(int16_t *) (G.gMarioStates + G.msYawOff) - 0x7000) & 0xE000);
}
