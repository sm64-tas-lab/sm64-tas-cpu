
#include <stdint.h>
typedef int16_t s16; typedef int32_t s32; typedef int8_t s8; typedef float f32;
struct Surface {
    s16 type; s16 force; s8 flags; s8 room; s16 lowerY; s16 upperY;
    s16 vertex1[3]; s16 vertex2[3]; s16 vertex3[3];
    struct { f32 x, y, z; } normal;
    f32 originOffset;
    void *object;
};
static f32 gScale = 4.0f;
__declspec(dllexport) void rh_set_scale(f32 s) { gScale = s; }
__declspec(dllexport) s32 rh_is_behind_surface(f32 *pos, struct Surface *surf) {
    f32 normX = (f32) ((surf->vertex2[1] - surf->vertex1[1]) * (surf->vertex3[2] - surf->vertex2[2]) -
                       (surf->vertex3[1] - surf->vertex2[1]) * (surf->vertex2[2] - surf->vertex1[2]));
    f32 normY = (f32) ((surf->vertex2[2] - surf->vertex1[2]) * (surf->vertex3[0] - surf->vertex2[0]) -
                       (surf->vertex3[2] - surf->vertex2[2]) * (surf->vertex2[0] - surf->vertex1[0]));
    f32 normZ = (f32) ((surf->vertex2[0] - surf->vertex1[0]) * (surf->vertex3[1] - surf->vertex2[1]) -
                       (surf->vertex3[0] - surf->vertex2[0]) * (surf->vertex2[1] - surf->vertex1[1]));
    f32 dirX = (f32) surf->vertex1[0] - pos[0] / gScale;
    f32 dirY = (f32) surf->vertex1[1] - pos[1] / gScale;
    f32 dirZ = (f32) surf->vertex1[2] - pos[2] / gScale;
    return (dirX * normX + dirY * normY + dirZ * normZ < 0) ? 1 : 0;
}
