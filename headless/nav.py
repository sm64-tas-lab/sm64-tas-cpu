"""A walking path between two points of the current area, around its walls: a grid of the static floors (cells of
CELL units, a layer for each floor height), steps of up to STEP_UP units up and DROP down, the walls crossed refused;
A* over it, then the path pulled straight (only the turns are kept)."""
import heapq
import math

CELL = 50.0
STEP_UP = 100.0          # (Mario walks up a step this high; higher needs a jump)
DROP = 400.0
WALL_LO, WALL_HI = 30.0, 150.0      # (the walls Mario's body meets: from 30 to 150 over his feet)
STEEP_NY = 0.7           # (a floor steeper than this slides Mario down: not walked)
BAD_FLOORS = {0x01, 0x0A, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27}   # (lava, death plane, quicksand)
BUCKET = 200.0
MARIO_R = 40.0           # (Mario's body: a wall nearer than this to his line stops him, crossed or not)
CLEAR, CLEAR_COST = 50.0, 400.0    # (Mario's body: a cell this near a wall costs more, kept for narrow ways)


class Nav:

    def __init__(self, g):
        self.layers, self.walls, self._near = {}, {}, {}
        for s in g.surfaces():
            v = (s['v1'], s['v2'], s['v3'])
            if s['kind'] == 'floor':
                if s['normal'][1] >= STEEP_NY and s['type'] not in BAD_FLOORS:
                    self._raster(v, s['normal'], s['origin'])
            elif s['kind'] == 'wall':
                self._wall(v)
        for v, n in object_surfaces(g):
            if n[1] > 0.01:
                if n[1] >= STEEP_NY:
                    self._raster(v, n, -(n[0] * v[0][0] + n[1] * v[0][1] + n[2] * v[0][2]))
            elif n[1] >= -0.01:
                self._wall(v)

    # ---- building ----------------------------------------------------------------------------------------------
    def _raster(self, v, n, origin):
        xs, zs = [p[0] for p in v], [p[2] for p in v]
        for i in range(int(math.floor(min(xs) / CELL)), int(math.floor(max(xs) / CELL)) + 1):
            for j in range(int(math.floor(min(zs) / CELL)), int(math.floor(max(zs) / CELL)) + 1):
                x, z = (i + 0.5) * CELL, (j + 0.5) * CELL
                if not _inside(x, z, v):
                    continue
                h = -(n[0] * x + n[2] * z + origin) / n[1]
                hs = self.layers.setdefault((i, j), [])
                if not any(abs(h - q) < 20.0 for q in hs):
                    hs.append(h)
                    hs.sort()

    def _wall(self, v):
        pts = [(p[0], p[2]) for p in v]
        a, b = max(((p, q) for p in pts for q in pts), key=lambda pq: (pq[0][0] - pq[1][0]) ** 2 + (pq[0][1] - pq[1][1]) ** 2)
        if a == b:
            return
        rec = (a, b, min(p[1] for p in v), max(p[1] for p in v))
        for bi in range(int(math.floor(min(a[0], b[0]) / BUCKET)), int(math.floor(max(a[0], b[0]) / BUCKET)) + 1):
            for bj in range(int(math.floor(min(a[1], b[1]) / BUCKET)), int(math.floor(max(a[1], b[1]) / BUCKET)) + 1):
                self.walls.setdefault((bi, bj), []).append(rec)

    # ---- queries -----------------------------------------------------------------------------------------------
    def cell(self, x, z):
        return int(math.floor(x / CELL)), int(math.floor(z / CELL))

    def layer(self, c, y, up=60.0):
        hs = self.layers.get(c)
        if not hs:
            return None
        below = [k for k, h in enumerate(hs) if h <= y + up]
        return below[-1] if below else None

    def blocked(self, p, q, h):
        """a wall Mario's body meets on the way p -> q (x, z) walking at height h"""
        lo, hi = h + WALL_LO, h + WALL_HI
        seen = set()
        for bi in range(int(math.floor(min(p[0], q[0]) / BUCKET)), int(math.floor(max(p[0], q[0]) / BUCKET)) + 1):
            for bj in range(int(math.floor(min(p[1], q[1]) / BUCKET)), int(math.floor(max(p[1], q[1]) / BUCKET)) + 1):
                for w in self.walls.get((bi, bj), ()):
                    if id(w) in seen or w[3] < lo or w[2] > hi:
                        continue
                    seen.add(id(w))
                    if _cross(p, q, w[0], w[1]) or _segs_dist(p, q, w[0], w[1]) < MARIO_R:
                        return True
        return False

    def near_wall(self, c, h):
        """a wall at Mario's body height within CLEAR units of the cell's middle"""
        key = (c, round(h))
        if key not in self._near:
            x, z = (c[0] + 0.5) * CELL, (c[1] + 0.5) * CELL
            lo, hi, hit = h + WALL_LO, h + WALL_HI, False
            for bi in range(int(math.floor((x - CLEAR) / BUCKET)), int(math.floor((x + CLEAR) / BUCKET)) + 1):
                for bj in range(int(math.floor((z - CLEAR) / BUCKET)), int(math.floor((z + CLEAR) / BUCKET)) + 1):
                    for w in self.walls.get((bi, bj), ()):
                        if w[3] >= lo and w[2] <= hi and _seg_dist(x, z, w[0], w[1]) < CLEAR:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            self._near[key] = hit
        return self._near[key]

    def _next(self, node):
        i, j, k = node
        h = self.layers[(i, j)][k]
        c0 = ((i + 0.5) * CELL, (j + 0.5) * CELL)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if not (di or dj):
                    continue
                hs = self.layers.get((i + di, j + dj))
                if not hs:
                    continue
                c1 = ((i + di + 0.5) * CELL, (j + dj + 0.5) * CELL)
                for k2, h2 in enumerate(hs):
                    if h2 - h > STEP_UP or h - h2 > DROP:
                        continue
                    if di and dj and not (self.layers.get((i + di, j)) and self.layers.get((i, j + dj))):
                        continue             # (no corner cut past a hole)
                    if self.blocked(c0, c1, max(h, h2)):
                        continue
                    cost = CELL * math.hypot(di, dj) + max(0.0, h2 - h)
                    if self.near_wall((i + di, j + dj), h2):
                        cost += CLEAR_COST
                    yield (i + di, j + dj, k2), cost

    def path(self, a, b, max_nodes=150000):
        """a -> b ((x, y, z) each; b's y may be None: any floor there) -> the turns [(x, y, z), ..., b] or None"""
        ca, cb = self.cell(a[0], a[2]), self.cell(b[0], b[2])
        ka = self.layer(ca, a[1])
        if ka is None:
            return None
        goal = set()
        for r in range(4):                  # (a point against a wall, a painting's foot: the nearest floor cells)
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    c = (cb[0] + di, cb[1] + dj)
                    goal |= {(c[0], c[1], k) for k in range(len(self.layers.get(c, ())))
                             if b[1] is None or abs(self.layers[c][k] - b[1]) < 150.0}
            if goal:
                break
        if not goal:
            return None
        s = (ca[0], ca[1], ka)
        cost, prev, q, n = {s: 0.0}, {}, [(0.0, s)], 0
        bx, bz = (cb[0] + 0.5) * CELL, (cb[1] + 0.5) * CELL
        while q and n < max_nodes:
            _, cur = heapq.heappop(q)
            if cur in goal:
                return self._pull(self._unwind(prev, cur), a, b)
            n += 1
            for nb, c in self._next(cur):
                nc = cost[cur] + c
                if nc < cost.get(nb, 1e18):
                    cost[nb], prev[nb] = nc, cur
                    heapq.heappush(q, (nc + math.hypot((nb[0] + 0.5) * CELL - bx, (nb[1] + 0.5) * CELL - bz), nb))
        return None

    def _unwind(self, prev, cur):
        out = [cur]
        while cur in prev:
            cur = prev[cur]
            out.append(cur)
        return [((i + 0.5) * CELL, self.layers[(i, j)][k], (j + 0.5) * CELL) for i, j, k in reversed(out)]

    def clear(self, p, q, keep_clear=False):
        """a straight walk p -> q: floor all along within a step of the last one, no wall met"""
        d = math.hypot(q[0] - p[0], q[2] - p[2])
        n = max(1, int(d / (CELL * 0.5)))
        h = p[1]
        for t in range(1, n + 1):
            x, z = p[0] + (q[0] - p[0]) * t / n, p[2] + (q[2] - p[2]) * t / n
            hs = self.layers.get(self.cell(x, z))
            if not hs:
                return False
            ok = [y for y in hs if y - h <= STEP_UP and h - y <= DROP]
            if not ok:
                return False
            h2 = max(ok)
            if self.blocked((p[0] + (q[0] - p[0]) * (t - 1) / n, p[2] + (q[2] - p[2]) * (t - 1) / n), (x, z),
                            max(h, h2)):
                return False
            if keep_clear and self.near_wall(self.cell(x, z), h2):
                return False
            h = h2
        return True

    def _pull(self, pts, a, b):
        pts = [tuple(a)] + pts[1:-1] + [(b[0], pts[-1][1], b[2])]
        out, i = [], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.clear(pts[i], pts[j], keep_clear=True):
                j -= 1
            out.append(pts[j])
            i = j
        return [tuple(round(c) for c in p) for p in out]


def _inside(x, z, v):
    (x1, _, z1), (x2, _, z2), (x3, _, z3) = v
    d1 = (x - x2) * (z1 - z2) - (x1 - x2) * (z - z2)
    d2 = (x - x3) * (z2 - z3) - (x2 - x3) * (z - z3)
    d3 = (x - x1) * (z3 - z1) - (x3 - x1) * (z - z1)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _seg_dist(x, z, a, b):
    ex, ez = b[0] - a[0], b[1] - a[1]
    n = ex * ex + ez * ez
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((x - a[0]) * ex + (z - a[1]) * ez) / n))
    return math.hypot(x - (a[0] + t * ex), z - (a[1] + t * ez))


def _segs_dist(p, q, a, b):
    return min(_seg_dist(p[0], p[1], a, b), _seg_dist(q[0], q[1], a, b), _seg_dist(a[0], a[1], p, q),
               _seg_dist(b[0], b[1], p, q))


def _cross(p, q, a, b):
    def o(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])
    return (o(p, q, a) * o(p, q, b) < 0) and (o(a, b, p) * o(a, b, q) < 0)


FORCE_TYPES = {0x04, 0x0E, 0x24, 0x25, 0x27, 0x2C, 0x2D}     # (decomp surface_load.c surface_has_force: a 4th value)
TERRAIN_LOAD_VERTICES, TERRAIN_LOAD_CONTINUE = 0x40, 0x41
DOORS = (1 << 2) | (1 << 11)     # (oInteractType INTERACT_DOOR | INTERACT_WARP_DOOR)


def _fields():
    import n64mem as NM
    o = {m[3]: m for m in NM.members(NM.find_type('Object'))}
    gfx = {m[3]: m for m in NM.members([m for m in NM.members(o['header'][2]) if m[3] == 'gfx'][0][2])}
    return o['collisionData'][1], o['transform'][1], gfx['scale'][1]


def _zxy(o, g, raw=216, face=0x12):
    """the object's transform from its position and face angles (decomp math_util.c mtxf_rotate_zxy_and_translate,
    object_helpers.c obj_build_transform_from_pos_and_angle)"""
    ang = [g.read(o['addr'] + raw + 4 * (face + i), 'i') * math.pi / 32768.0 for i in range(3)]
    sx, cx, sy, cy, sz, cz = (math.sin(ang[0]), math.cos(ang[0]), math.sin(ang[1]), math.cos(ang[1]),
                              math.sin(ang[2]), math.cos(ang[2]))
    return [[cy * cz + sx * sy * sz, cx * sz, -sy * cz + sx * cy * sz, 0],
            [-cy * sz + sx * sy * cz, cx * cz, sy * sz + sx * cy * cz, 0],
            [cx * sy, -sx, cx * cy, 0],
            [o['pos_x'], o['pos_y'], o['pos_z'], 1]]


def object_surfaces(g):
    """the collision of the objects that carry one (gates, cage doors, platforms...), placed as the game places it
    (decomp surface_load.c load_object_collision_model / transform_object_vertices): the game loads it only near
    Mario, path needs it from afar -> [(3 vertices, normal)]"""
    import ctypes
    try:
        off_col, off_mtx, off_scale = _fields()
    except Exception:
        return []
    out = []
    for o in g.objects():
        col = g.read(o['addr'] + off_col, 'Q')
        if not col or g.read(o['addr'] + 216 + 4 * 0x2A, 'I') & DOORS:
            continue                        # (a door opens as Mario walks in: not a wall for the path)
        try:
            m = [[g.read(o['addr'] + off_mtx + 16 * i + 4 * j, 'f') for j in range(4)] for i in range(4)]
            sc = [g.read(o['addr'] + off_scale + 4 * i, 'f') for i in range(3)]
            if math.dist(m[3][:3], (o['pos_x'], o['pos_y'], o['pos_z'])) > 50.0:
                # (not built yet, or another object's: the game builds it only near Mario; from its position and facing)
                m = _zxy(o, g)
            m = [[m[i][j] * sc[i] for j in range(3)] + [m[i][3]] for i in range(3)] + [m[3]]
            h = lambda k: ctypes.c_short.from_address(col + 2 * k).value
            k = 0
            if h(k) != TERRAIN_LOAD_VERTICES:
                continue
            nv = h(k + 1)
            k += 2
            if not 0 < nv < 600:
                continue
            vs = []
            for _ in range(nv):
                x, y_, z = h(k), h(k + 1), h(k + 2)
                k += 3
                vs.append(tuple(x * m[0][c] + y_ * m[1][c] + z * m[2][c] + m[3][c] for c in range(3)))
            for _ in range(64):
                t = h(k)
                if t == TERRAIN_LOAD_CONTINUE:
                    break
                n = h(k + 1)
                k += 2
                step = 4 if t in FORCE_TYPES else 3
                for _ in range(max(0, n)):
                    a, b, c = (vs[h(k + q)] if 0 <= h(k + q) < nv else None for q in range(3))
                    k += step
                    if a is None or b is None or c is None:
                        continue
                    nx = (b[1] - a[1]) * (c[2] - b[2]) - (b[2] - a[2]) * (c[1] - b[1])
                    ny = (b[2] - a[2]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[2] - b[2])
                    nz = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
                    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
                    if ln > 1e-6:
                        out.append(((a, b, c), (nx / ln, ny / ln, nz / ln)))
        except (OSError, ValueError, IndexError):
            continue
    return out


_CACHE = {}


def for_game(g):
    """the grid of the area Mario is in, built once (~1 s) for each level / area"""
    m = g.mario()
    key = (m['level'], m['area'], g.read('gNumStaticSurfaces', 'i'))
    if key not in _CACHE:
        if len(_CACHE) > 8:
            _CACHE.clear()
        _CACHE[key] = Nav(g)
    return _CACHE[key]
