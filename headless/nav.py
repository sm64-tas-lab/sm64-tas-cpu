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


class Nav:

    def __init__(self, g):
        self.layers, self.walls = {}, {}
        for s in g.surfaces():
            v = (s['v1'], s['v2'], s['v3'])
            if s['kind'] == 'floor':
                if s['normal'][1] >= STEEP_NY and s['type'] not in BAD_FLOORS:
                    self._raster(v, s['normal'], s['origin'])
            elif s['kind'] == 'wall':
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
                    if _cross(p, q, w[0], w[1]):
                        return True
        return False

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
                    yield (i + di, j + dj, k2), CELL * math.hypot(di, dj) + max(0.0, h2 - h)

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

    def clear(self, p, q):
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
            h = h2
        return True

    def _pull(self, pts, a, b):
        pts = [tuple(a)] + pts[1:-1] + [(b[0], pts[-1][1], b[2])]
        out, i = [], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.clear(pts[i], pts[j]):
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


def _cross(p, q, a, b):
    def o(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])
    return (o(p, q, a) * o(p, q, b) < 0) and (o(a, b, p) * o(a, b, q) < 0)


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
