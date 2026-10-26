
#include <math.h>
#include <stdint.h>

typedef struct {
    int16_t rawStickX, rawStickY;
    float stickX, stickY, stickMag;
    uint16_t buttonDown, buttonPressed;
} Controller;

typedef struct {
    void **gCurrentObject;
    char *gMarioStates;
    int64_t msPosOff;
    Controller *gControllers;
    Controller **gPlayer1Controller;
    int16_t *sCameraHUD;
    int16_t *s8DirModeYawOffset;
    int16_t *gCurrLevelNum;
    int16_t *gCurrAreaIndex;
    int64_t objRawOff;
    int32_t (*set_cam_angle)(int32_t);
    void (*radial_camera_input)(void *, float);
    void (*adjust_analog_stick)(Controller *);
} Globals;

static Globals G;
static uint8_t gFlags[64 * 16];

__declspec(dllexport) void rh_init(const Globals *g) { G = *g; }

__declspec(dllexport) void rh_set_flags(const uint8_t *flags) {
    for (int i = 0; i < 64 * 16; i++) gFlags[i] = flags[i];
}

static int area_flag(void) {
    int lvl = *G.gCurrLevelNum, area = *G.gCurrAreaIndex;
    if (lvl < 0 || lvl >= 64 || area < 0 || area >= 16) return 0;
    return gFlags[lvl * 16 + area] & 1;
}


__declspec(dllexport) void rh_adjust_analog_stick(Controller *c) {
    if (!area_flag()) {
        G.adjust_analog_stick(c);
        return;
    }
    c->stickX = 0;
    c->stickY = 0;
    if (c->rawStickX <= -8) c->stickX = (float) (c->rawStickX + 6);
    if (c->rawStickX >= 8) c->stickX = (float) (c->rawStickX - 6);
    c->stickMag = sqrtf(c->stickX * c->stickX + c->stickY * c->stickY);
    if (c->stickMag > 64) {
        c->stickX *= 64 / c->stickMag;
        c->stickY *= 64 / c->stickMag;
        c->stickMag = 64;
    }
}


__declspec(dllexport) void rh_mode8_radial(void *c, float unused) {
    G.radial_camera_input(c, unused);
    if (area_flag()) {
        uint16_t b = (*G.gPlayer1Controller)->buttonPressed;
        if (b & 0x0001) *G.s8DirModeYawOffset -= 0x2000;
        if (b & 0x0002) *G.s8DirModeYawOffset += 0x2000;
    }
}


__declspec(dllexport) void rh_finish_line_2d(void) {
    char *o = (char *) *G.gCurrentObject;
    uint32_t *posX = (uint32_t *) (G.gMarioStates + G.msPosOff);
    int32_t *timer = (int32_t *) (o + G.objRawOff + 4 * 0x33);
    uint32_t *saved = (uint32_t *) (o + G.objRawOff + 4 * 0x3E);
    uint32_t x;
    G.gControllers[0].buttonDown |= 0x0010;
    x = *posX;
    if (*timer < 2) *saved = x;
    *posX = *saved;
    if (G.sCameraHUD) *G.sCameraHUD = 10;
    *G.s8DirModeYawOffset = 0x4000;
    G.set_cam_angle(2);
}
