
#include <math.h>
#include <stdint.h>

typedef int16_t s16; typedef int32_t s32; typedef int8_t s8; typedef float f32;

struct Surface {
    s16 type; s16 force; s8 flags; s8 room; s16 lowerY; s16 upperY;
    s16 vertex1[3]; s16 vertex2[3]; s16 vertex3[3];
    struct { f32 x, y, z; } normal;
    f32 originOffset;
    void *object;
};
struct SurfaceNode { struct SurfaceNode *next; struct Surface *surface; };
typedef struct SurfaceNode SpatialPartitionCell[3];
struct WallCollisionData { f32 x, y, z, offsetY, radius; s16 filler; s16 numWalls; struct Surface *walls[4]; };
struct FloorGeometry { f32 filler[4]; f32 normalX, normalY, normalZ, originOffset; };

enum { FLOORS = 0, CEILS = 1, WALLS = 2 };
#define CELL(v) ((((v) + 0x2000) / (1 << 10)) & 15)


static SpatialPartitionCell (*gStatic)[16], (*gDynamic)[16];
static s16 *gCheckingCam, *gIncludeIntangible;
static char **gCurrentObject, **gMarioObject, **gMarioState;
static s32 *gSurfacesAllocated, *gSurfaceNodesAllocated;
static struct Surface **sSurfacePool;
static struct SurfaceNode **sSurfaceNodePool;
static s16 *gNumCalls;
static s32 *gNumFindFloorMisses;
static struct FloorGeometry *sFloorGeo;
static void *bhvDDDWarp;
static s32 oActiveFlags, oBehavior;

struct RhGlobals {
    void *staticPart, *dynamicPart, *checkingCam, *includeIntangible, *currentObject, *marioObject, *marioState,
         *surfacesAllocated, *surfaceNodesAllocated, *surfacePool, *surfaceNodePool, *numCalls, *numFindFloorMisses,
         *floorGeo, *bhvDDDWarp;
    s32 oActiveFlags, oBehavior;
};

__declspec(dllexport) void rh_init(struct RhGlobals *g) {
    gStatic = g->staticPart; gDynamic = g->dynamicPart;
    gCheckingCam = g->checkingCam; gIncludeIntangible = g->includeIntangible;
    gCurrentObject = g->currentObject; gMarioObject = g->marioObject; gMarioState = g->marioState;
    gSurfacesAllocated = g->surfacesAllocated; gSurfaceNodesAllocated = g->surfaceNodesAllocated;
    sSurfacePool = g->surfacePool; sSurfaceNodePool = g->surfaceNodePool;
    gNumCalls = g->numCalls; gNumFindFloorMisses = g->numFindFloorMisses;
    sFloorGeo = g->floorGeo; bhvDDDWarp = g->bhvDDDWarp;
    oActiveFlags = g->oActiveFlags; oBehavior = g->oBehavior;
}


static s32 find_wall_collisions_from_list(struct SurfaceNode *surfaceNode, struct WallCollisionData *data) {
    struct Surface *surf;
    f32 offset;
    f32 radius = data->radius / 4.0f;
    f32 x = data->x / 4.0f;
    f32 y = data->y / 4.0f + data->offsetY / 4.0f;
    f32 z = data->z / 4.0f;
    f32 px, pz, w1, w2, w3, y1, y2, y3;
    s32 numCols = 0;
    if (radius > 200.0f) radius = 200.0f;
    while (surfaceNode != 0) {
        surf = surfaceNode->surface;
        surfaceNode = surfaceNode->next;
        if (y < surf->lowerY || y > surf->upperY) continue;
        offset = surf->normal.x * x + surf->normal.y * y + surf->normal.z * z + surf->originOffset;
        if (offset < -radius || offset > radius) continue;
        px = x; pz = z;
        if (surf->flags & (1 << 3)) {
            w1 = -surf->vertex1[2]; w2 = -surf->vertex2[2]; w3 = -surf->vertex3[2];
            y1 = surf->vertex1[1]; y2 = surf->vertex2[1]; y3 = surf->vertex3[1];
            if (surf->normal.x > 0.0f) {
                if ((y1 - y) * (w2 - w1) - (w1 - -pz) * (y2 - y1) > 0.0f) continue;
                if ((y2 - y) * (w3 - w2) - (w2 - -pz) * (y3 - y2) > 0.0f) continue;
                if ((y3 - y) * (w1 - w3) - (w3 - -pz) * (y1 - y3) > 0.0f) continue;
            } else {
                if ((y1 - y) * (w2 - w1) - (w1 - -pz) * (y2 - y1) < 0.0f) continue;
                if ((y2 - y) * (w3 - w2) - (w2 - -pz) * (y3 - y2) < 0.0f) continue;
                if ((y3 - y) * (w1 - w3) - (w3 - -pz) * (y1 - y3) < 0.0f) continue;
            }
        } else {
            w1 = surf->vertex1[0]; w2 = surf->vertex2[0]; w3 = surf->vertex3[0];
            y1 = surf->vertex1[1]; y2 = surf->vertex2[1]; y3 = surf->vertex3[1];
            if (surf->normal.z > 0.0f) {
                if ((y1 - y) * (w2 - w1) - (w1 - px) * (y2 - y1) > 0.0f) continue;
                if ((y2 - y) * (w3 - w2) - (w2 - px) * (y3 - y2) > 0.0f) continue;
                if ((y3 - y) * (w1 - w3) - (w3 - px) * (y1 - y3) > 0.0f) continue;
            } else {
                if ((y1 - y) * (w2 - w1) - (w1 - px) * (y2 - y1) < 0.0f) continue;
                if ((y2 - y) * (w3 - w2) - (w2 - px) * (y3 - y2) < 0.0f) continue;
                if ((y3 - y) * (w1 - w3) - (w3 - px) * (y1 - y3) < 0.0f) continue;
            }
        }
        if (*gCheckingCam) {
            if (surf->flags & (1 << 1)) continue;
        } else {
            if (surf->type == 0x0072) continue;
            if (surf->type == 0x007B) {
                char *o = *gCurrentObject;
                if (o != 0 && (*(s16 *) (o + oActiveFlags) & (1 << 6))) continue;
                if (o != 0 && o == *gMarioObject && (*(uint32_t *) (*gMarioState + 4) & 0x00000002)) continue;
            }
        }
        data->x += surf->normal.x * ((radius - offset) * 4.0f);
        data->z += surf->normal.z * ((radius - offset) * 4.0f);
        if (data->numWalls < 4) data->walls[data->numWalls++] = surf;
        numCols++;
    }
    return numCols;
}

__declspec(dllexport) s32 rh_find_wall_collisions(struct WallCollisionData *colData) {
    struct SurfaceNode *node;
    s16 cellX, cellZ;
    s32 numCollisions = 0;
    s16 x = (s16) (((s32) colData->x) >> 2);
    s16 z = (s16) (((s32) colData->z) >> 2);
    colData->numWalls = 0;
    if (x < -0x8000 || x >= 0x7FFF) return numCollisions;
    if (z < -0x8000 || z >= 0x7FFF) return numCollisions;
    cellX = CELL(x);
    cellZ = CELL(z);
    node = gDynamic[cellZ][cellX][WALLS].next;
    numCollisions += find_wall_collisions_from_list(node, colData);
    node = gStatic[cellZ][cellX][WALLS].next;
    numCollisions += find_wall_collisions_from_list(node, colData);
    gNumCalls[2]++;
    return numCollisions;
}

__declspec(dllexport) s32 rh_f32_find_wall_collision(f32 *xPtr, f32 *yPtr, f32 *zPtr, f32 offsetY, f32 radius) {
    struct WallCollisionData collision;
    s32 numCollisions = 0;
    collision.offsetY = offsetY;
    collision.radius = radius;
    collision.x = *xPtr;
    collision.y = *yPtr;
    collision.z = *zPtr;
    collision.numWalls = 0;
    numCollisions = rh_find_wall_collisions(&collision);
    *xPtr = collision.x;
    *yPtr = collision.y;
    *zPtr = collision.z;
    return numCollisions;
}


static struct Surface *find_ceil_from_list(struct SurfaceNode *surfaceNode, s32 x, s32 y, s32 z, f32 *pheight) {
    struct Surface *surf, *ceil = 0;
    s32 x1, z1, x2, z2, x3, z3;
    while (surfaceNode != 0) {
        surf = surfaceNode->surface;
        surfaceNode = surfaceNode->next;
        x1 = surf->vertex1[0]; z1 = surf->vertex1[2]; z2 = surf->vertex2[2]; x2 = surf->vertex2[0];
        if ((z1 - z) * (x2 - x1) - (x1 - x) * (z2 - z1) > 0) continue;
        x3 = surf->vertex3[0]; z3 = surf->vertex3[2];
        if ((z2 - z) * (x3 - x2) - (x2 - x) * (z3 - z2) > 0) continue;
        if ((z3 - z) * (x1 - x3) - (x3 - x) * (z1 - z3) > 0) continue;
        if (*gCheckingCam != 0) {
            if (surf->flags & (1 << 1)) continue;
        } else if (surf->type == 0x0072) continue;
        {
            f32 nx = surf->normal.x, ny = surf->normal.y, nz = surf->normal.z, oo = surf->originOffset;
            f32 height;
            if (ny == 0.0f) continue;
            height = -(x * nx + nz * z + oo) / ny;
            height = height + height;
            height = height + height;
            if (y - (height - -78.0f) > 0.0f) continue;
            *pheight = height;
            ceil = surf;
            break;
        }
    }
    return ceil;
}

__declspec(dllexport) f32 rh_find_ceil(f32 posX, f32 posY, f32 posZ, struct Surface **pceil) {
    s16 cellZ, cellX;
    struct Surface *ceil, *dynamicCeil;
    struct SurfaceNode *surfaceList;
    f32 height = 20000.0f, dynamicHeight = 20000.0f;
    s16 x = (s16) (((s32) posX) >> 2);
    s16 y = (s16) (s32) posY;
    s16 z = (s16) (((s32) posZ) >> 2);
    *pceil = 0;
    if (x < -0x8000 || x >= 0x7FFF) return height;
    if (z < -0x8000 || z >= 0x7FFF) return height;
    cellX = CELL(x);
    cellZ = CELL(z);
    surfaceList = gDynamic[cellZ][cellX][CEILS].next;
    dynamicCeil = find_ceil_from_list(surfaceList, x, y, z, &dynamicHeight);
    surfaceList = gStatic[cellZ][cellX][CEILS].next;
    ceil = find_ceil_from_list(surfaceList, x, y, z, &height);
    if (dynamicHeight < height) {
        ceil = dynamicCeil;
        height = dynamicHeight;
    }
    *pceil = ceil;
    gNumCalls[1]++;
    return height;
}


static s32 gFloorHighest;

__declspec(dllexport) void rh_set_floor_highest(s32 on) { gFloorHighest = on; }

static struct Surface *find_floor_from_list(struct SurfaceNode *surfaceNode, s32 x, s32 y, s32 z, f32 *pheight) {
    struct Surface *surf, *floor = 0;
    s32 x1, z1, x2, z2, x3, z3;
    f32 nx, ny, nz, oo, height;
    union { uint32_t u; f32 f; } best = { 0xDFFF0000u };
    while (surfaceNode != 0) {
        surf = surfaceNode->surface;
        surfaceNode = surfaceNode->next;
        x1 = surf->vertex1[0]; z1 = surf->vertex1[2]; x2 = surf->vertex2[0]; z2 = surf->vertex2[2];
        if ((z1 - z) * (x2 - x1) - (x1 - x) * (z2 - z1) < 0) continue;
        x3 = surf->vertex3[0]; z3 = surf->vertex3[2];
        if ((z2 - z) * (x3 - x2) - (x2 - x) * (z3 - z2) < 0) continue;
        if ((z3 - z) * (x1 - x3) - (x3 - x) * (z1 - z3) < 0) continue;
        if (*gCheckingCam != 0) {
            if (surf->flags & (1 << 1)) continue;
        } else if (surf->type == 0x0072) continue;
        nx = surf->normal.x; ny = surf->normal.y; nz = surf->normal.z; oo = surf->originOffset;
        if (ny == 0.0f) continue;
        height = -(x * nx + nz * z + oo) / ny;
        height = height + height;
        height = height + height;
        if (gFloorHighest && height < best.f) continue;
        if (y - (height + -78.0f) < 0.0f) continue;
        *pheight = height;
        floor = surf;
        if (!gFloorHighest)
            break;
        best.f = height;
    }
    return floor;
}

__declspec(dllexport) f32 rh_find_floor(f32 xPos, f32 yPos, f32 zPos, struct Surface **pfloor) {
    s16 cellZ, cellX;
    struct Surface *floor, *dynamicFloor;
    struct SurfaceNode *surfaceList;
    f32 height = -11000.0f, dynamicHeight = -11000.0f;
    s16 x = (s16) (((s32) xPos) >> 2);
    s16 y = (s16) (s32) yPos;
    s16 z = (s16) (((s32) zPos) >> 2);
    *pfloor = 0;
    if (x < -0x8000 || x >= 0x7FFF) return height;
    if (z < -0x8000 || z >= 0x7FFF) return height;
    cellX = CELL(x);
    cellZ = CELL(z);
    surfaceList = gDynamic[cellZ][cellX][FLOORS].next;
    dynamicFloor = find_floor_from_list(surfaceList, x, y, z, &dynamicHeight);
    surfaceList = gStatic[cellZ][cellX][FLOORS].next;
    floor = find_floor_from_list(surfaceList, x, y, z, &height);
    if (!*gIncludeIntangible) {
        if (floor != 0 && floor->type == 0x0012)
            floor = find_floor_from_list(surfaceList, x, (s32) (height - 200.0f), z, &height);
    } else {
        *gIncludeIntangible = 0;
    }
    if (floor == 0) (*gNumFindFloorMisses)++;
    if (dynamicHeight > height) {
        floor = dynamicFloor;
        height = dynamicHeight;
    }
    *pfloor = floor;
    gNumCalls[0]++;
    return height;
}

__declspec(dllexport) f32 rh_find_floor_height(f32 x, f32 y, f32 z) {
    struct Surface *floor;
    return rh_find_floor(x, y, z, &floor);
}

__declspec(dllexport) f32 rh_find_floor_height_and_data(f32 xPos, f32 yPos, f32 zPos, struct FloorGeometry **floorGeo) {
    struct Surface *floor;
    f32 floorHeight = rh_find_floor(xPos, yPos, zPos, &floor);
    *floorGeo = 0;
    if (floor != 0) {
        f32 oo = floor->originOffset;
        oo = oo + oo;
        oo = oo + oo;
        sFloorGeo->normalX = floor->normal.x;
        sFloorGeo->normalY = floor->normal.y;
        sFloorGeo->normalZ = floor->normal.z;
        sFloorGeo->originOffset = oo;
        *floorGeo = sFloorGeo;
    }
    return floorHeight;
}

__declspec(dllexport) f32 rh_unused_find_dynamic_floor(f32 xPos, f32 yPos, f32 zPos, struct Surface **pfloor) {
    struct Surface *floor;
    f32 floorHeight = -11000.0f;
    s16 x = (s16) (((s32) xPos) >> 2);
    s16 y = (s16) (s32) yPos;
    s16 z = (s16) (((s32) zPos) >> 2);
    s16 cellX = CELL(x), cellZ = CELL(z);
    floor = find_floor_from_list(gDynamic[cellZ][cellX][FLOORS].next, x, y, z, &floorHeight);
    *pfloor = floor;
    return floorHeight;
}


static struct SurfaceNode *alloc_surface_node(void) {
    struct SurfaceNode *node = &(*sSurfaceNodePool)[*gSurfaceNodesAllocated];
    (*gSurfaceNodesAllocated)++;
    node->next = 0;
    return node;
}

static struct Surface *alloc_surface(void) {
    struct Surface *surface = &(*sSurfacePool)[*gSurfacesAllocated];
    (*gSurfacesAllocated)++;
    surface->type = 0; surface->force = 0; surface->flags = 0; surface->room = 0; surface->object = 0;
    return surface;
}

static void add_surface_to_cell(s16 dynamic, s16 cellX, s16 cellZ, struct Surface *surface) {
    struct SurfaceNode *newNode = alloc_surface_node();
    struct SurfaceNode *list;
    s16 surfacePriority, priority, sortDir, listIndex;
    if (surface->normal.y > 0.01) {
        listIndex = FLOORS; sortDir = 1;
    } else if (surface->normal.y < -0.01) {
        listIndex = CEILS; sortDir = -1;
    } else {
        listIndex = WALLS; sortDir = 0;
        if (surface->normal.x < -0.707 || surface->normal.x > 0.707) surface->flags |= 1 << 3;
    }
    surfacePriority = surface->vertex1[1] * sortDir;
    newNode->surface = surface;
    list = dynamic ? &gDynamic[cellZ][cellX][listIndex] : &gStatic[cellZ][cellX][listIndex];
    while (list->next != 0) {
        priority = list->next->surface->vertex1[1] * sortDir;
        if (surfacePriority > priority) break;
        list = list->next;
    }
    newNode->next = list->next;
    list->next = newNode;
}

static s16 min_3(s16 a0, s16 a1, s16 a2) { if (a1 < a0) a0 = a1; if (a2 < a0) a0 = a2; return a0; }
static s16 max_3(s16 a0, s16 a1, s16 a2) { if (a1 > a0) a0 = a1; if (a2 > a0) a0 = a2; return a0; }

static s16 lower_cell_index(s16 coord) {
    s16 index;
    coord += 0x2000;
    if (coord < 0) coord = 0;
    index = coord / (1 << 10);
    if (coord % (1 << 10) < 50) index--;
    if (index < 0) index = 0;
    return index;
}

static s16 upper_cell_index(s16 coord) {
    s16 index;
    coord += 0x2000;
    if (coord < 0) coord = 0;
    index = coord / (1 << 10);
    if (coord % (1 << 10) > (1 << 10) - 50) index++;
    if (index > 15) index = 15;
    return index;
}

static void add_surface(struct Surface *surface, s32 dynamic) {
    s16 minX, minZ, maxX, maxZ, minCellX, minCellZ, maxCellX, maxCellZ, cellZ, cellX;
    minX = min_3(surface->vertex1[0], surface->vertex2[0], surface->vertex3[0]);
    minZ = min_3(surface->vertex1[2], surface->vertex2[2], surface->vertex3[2]);
    maxX = max_3(surface->vertex1[0], surface->vertex2[0], surface->vertex3[0]);
    maxZ = max_3(surface->vertex1[2], surface->vertex2[2], surface->vertex3[2]);
    minCellX = lower_cell_index(minX);
    maxCellX = upper_cell_index(maxX);
    minCellZ = lower_cell_index(minZ);
    maxCellZ = upper_cell_index(maxZ);
    for (cellZ = minCellZ; cellZ <= maxCellZ; cellZ++)
        for (cellX = minCellX; cellX <= maxCellX; cellX++)
            add_surface_to_cell(dynamic, cellX, cellZ, surface);
}

static struct Surface *read_surface_data(s16 *vertexData, s16 **vertexIndices) {
    struct Surface *surface;
    s32 x1, y1, z1, x2, y2, z2, x3, y3, z3, maxY, minY;
    f32 nx, ny, nz, mag;
    s16 offset1 = 3 * (*vertexIndices)[0], offset2 = 3 * (*vertexIndices)[1], offset3 = 3 * (*vertexIndices)[2];
    x1 = vertexData[offset1 + 0] >> 2; y1 = vertexData[offset1 + 1] >> 2; z1 = vertexData[offset1 + 2] >> 2;
    x2 = vertexData[offset2 + 0] >> 2; y2 = vertexData[offset2 + 1] >> 2; z2 = vertexData[offset2 + 2] >> 2;
    x3 = vertexData[offset3 + 0] >> 2; y3 = vertexData[offset3 + 1] >> 2; z3 = vertexData[offset3 + 2] >> 2;
    nx = (y2 - y1) * (z3 - z2) - (z2 - z1) * (y3 - y2);
    ny = (z2 - z1) * (x3 - x2) - (x2 - x1) * (z3 - z2);
    nz = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2);
    mag = sqrtf(nx * nx + ny * ny + nz * nz);
    minY = y1; if (y2 < minY) minY = y2; if (y3 < minY) minY = y3;
    maxY = y1; if (y2 > maxY) maxY = y2; if (y3 > maxY) maxY = y3;
    if (mag < 0.0001) return 0;
    mag = (f32) (1.0 / mag);
    nx *= mag; ny *= mag; nz *= mag;
    surface = alloc_surface();
    surface->vertex1[0] = x1; surface->vertex2[0] = x2; surface->vertex3[0] = x3;
    surface->vertex1[1] = y1; surface->vertex2[1] = y2; surface->vertex3[1] = y3;
    surface->vertex1[2] = z1; surface->vertex2[2] = z2; surface->vertex3[2] = z3;
    surface->normal.x = nx; surface->normal.y = ny; surface->normal.z = nz;
    surface->originOffset = -(nx * x1 + ny * y1 + nz * z1);
    surface->lowerY = minY - 5;
    surface->upperY = maxY + 5;
    return surface;
}

static s32 surface_has_force(s16 t) {
    return t == 0x0004 || t == 0x000E || t == 0x0024 || t == 0x0025 || t == 0x0027 || t == 0x002C || t == 0x002D;
}

static s32 surf_has_no_cam_collision(s16 t) {
    return (t == 0x0076 || t == 0x0077 || t == 0x0078 || t == 0x007A) ? (1 << 1) : 0;
}

__declspec(dllexport) void rh_load_object_surfaces(s16 **data, s16 *vertexData) {
    s32 surfaceType, i, numSurfaces;
    s16 hasForce, flags, room;
    char *obj = *gCurrentObject;
    surfaceType = *(*data); (*data)++;
    numSurfaces = *(*data); (*data)++;
    hasForce = surface_has_force(surfaceType);
    flags = surf_has_no_cam_collision(surfaceType);
    flags |= 1 << 0;
    room = (*(void **) (obj + oBehavior) == bhvDDDWarp) ? 5 : 0;
    for (i = 0; i < numSurfaces; i++) {
        struct Surface *surface = read_surface_data(vertexData, data);
        if (surface != 0) {
            surface->object = obj;
            surface->type = surfaceType;
            surface->force = hasForce ? *((*data) + 3) : 0;
            surface->flags |= flags;
            surface->room = (s8) room;
            add_surface(surface, 1);
        }
        *data += hasForce ? 4 : 3;
    }
}
