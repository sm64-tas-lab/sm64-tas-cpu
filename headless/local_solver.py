import ctypes
import math
import multiprocessing as mp
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import macros as MC

BUTTONS = ('', 'A', 'B', 'Z', 'AB', 'AZ', 'BZ', 'ABZ')
_HI = {'A': 0x80, 'B': 0x40, 'Z': 0x20}


def hi_byte(buttons):
    return sum(_HI[c] for c in buttons)


def _atan2s(tab, y, x):
    f32 = np.float32

    def look(a, b):
        if b == 0:
            return int(tab[0])
        return int(tab[int(f32(f32(f32(a) / f32(b)) * f32(1024)) + f32(0.5))])
    y, x = f32(y), f32(x)
    if x >= 0:
        if y >= 0:
            r = look(x, y) if y >= x else 0x4000 - look(y, x)
        else:
            y = -y
            r = 0x4000 + look(y, x) if y < x else 0x8000 - look(x, y)
    else:
        x = -x
        if y < 0:
            y = -y
            r = 0x8000 + look(x, y) if y >= x else 0xC000 - look(y, x)
        else:
            r = 0xC000 + look(y, x) if y < x else -look(x, y)
    return r & 0xFFFF


def _processed(rx, ry):
    f32 = np.float32
    sx = f32(rx + 6) if rx <= -8 else (f32(rx - 6) if rx >= 8 else f32(0))
    sy = f32(ry + 6) if ry <= -8 else (f32(ry - 6) if ry >= 8 else f32(0))
    mag = f32(np.sqrt(f32(sx * sx + sy * sy)))
    if mag > 64:
        sx, sy, mag = f32(sx * f32(f32(64) / mag)), f32(sy * f32(f32(64) / mag)), f32(64)
    return sx, sy, mag


_CLASSES = {}


def stick_classes(g, mode='intended'):
    if mode in _CLASSES:
        return _CLASSES[mode]
    if mode is None:
        out = [(x, y) for x in range(-128, 128) for y in range(-128, 128)]
    else:
        if getattr(g, 'n64', False):
            a = g.sym('gArctanTable')
            tab = [g.read(a + 2 * i, 'H') for i in range(1025)]
        else:
            tab = (ctypes.c_uint16 * 1025).from_address(g.addr('gArctanTable'))
        seen, out = {}, []
        for x in range(-128, 128):
            for y in range(-128, 128):
                sx, sy, mag = _processed(x, y)
                if mode == 'processed':
                    k = (float(sx), float(sy))
                else:
                    m = float(np.float32(np.float32(mag / np.float32(64)) * np.float32(mag / np.float32(64))) * 64)
                    k = (m, _atan2s(tab, -sy, sx) if m > 0 else None)
                if k not in seen:
                    seen[k] = (x, y)
                    out.append((x, y))
    _CLASSES[mode] = out
    return out


def coarse_sticks(n=32):
    out = [(0, 0)]
    for k in range(n):
        a = 2 * math.pi * k / n
        out.append((int(round(127 * math.cos(a))), int(round(127 * math.sin(a)))))
    return out


class View:

    _M = None

    N64_MS = {'action': 0x0C, 'forwardVel': 0x54, 'faceAngle': 0x2C, 'pos': 0x3C, 'vel': 0x48, 'squishTimer': 0xB4}

    def __init__(self, g):
        self.g = g
        self.n64 = getattr(g, 'n64', False)
        if self.n64:
            self.M = View.N64_MS
            self.n64_ms = 0x8033B170
            self.ms = g.host(self.n64_ms)
            self._face = g.host(self.n64_ms + 0x2C + 2, 2)
            self._squish = g.host(self.n64_ms + 0xB4, 1)
            return
        self.ms = g.addr('gMarioStates')
        if View._M is None:
            import n64mem as NM
            View._M = {m[3]: m[1] for m in NM.members(NM.find_type('MarioState'))}
        self.M = View._M
        self._face = self.ms + self.M['faceAngle'] + 2
        self._squish = self.ms + self.M['squishTimer']

    def action(self):
        return ctypes.c_uint32.from_address(self.ms + self.M['action']).value

    def fv(self):
        return ctypes.c_float.from_address(self.ms + self.M['forwardVel']).value

    def pos(self):
        return list((ctypes.c_float * 3).from_address(self.ms + self.M['pos']))

    def vel(self):
        return list((ctypes.c_float * 3).from_address(self.ms + self.M['vel']))

    def face(self):
        return ctypes.c_int16.from_address(self._face).value & 0xFFFF

    def squish(self):
        return ctypes.c_uint8.from_address(self._squish).value


def snapshot(v):
    return {'pos': v.pos(), 'fv': v.fv(), 'face': v.face(), 'action': v.action(), 'vy': v.vel()[1]}


TOL = {'pos': 2.0, 'fv': 0.5, 'face': 0x80, 'vy': 1.0}


def near_dist(v, target):
    p, t = v.pos(), target['pos']
    d = math.dist(p, t) + 4 * abs(v.fv() - target['fv']) + 2 * abs(v.vel()[1] - target.get('vy', v.vel()[1]))
    d += abs(((v.face() - target['face'] + 0x8000) & 0xFFFF) - 0x8000) / 64.0
    return d + (0 if v.action() == target['action'] else 500)


def is_near(v, target, tol=None):
    tol = tol or TOL
    return (v.action() == target['action'] and math.dist(v.pos(), target['pos']) <= tol['pos']
            and abs(v.fv() - target['fv']) <= tol['fv'] and abs(v.vel()[1] - target.get('vy', v.vel()[1])) <= tol['vy']
            and abs(((v.face() - target['face'] + 0x8000) & 0xFFFF) - 0x8000) <= tol['face'])


class Cond:

    def __init__(self, kind, arg):
        self.kind, self.arg = kind, arg

    def __call__(self, g, v):
        if self.kind == 'action':
            return MC.NAME.get(v.action()) in self.arg
        if self.kind == 'near':
            return is_near(v, self.arg[0], self.arg[1])
        return _resolve(self.arg)(g, v)


class Score:

    def __init__(self, kind, arg=None):
        self.kind, self.arg = kind, arg

    def __call__(self, g, v):
        if self.kind == 'fv':
            return v.fv()
        if self.kind == 'vy':
            return v.vel()[1]
        if self.kind == 'height':
            return v.pos()[1]
        if self.kind == 'action':
            return self.arg.get(MC.NAME.get(v.action()), 0)
        if self.kind == 'near':
            return -near_dist(v, self.arg)
        if self.kind == 'toward':
            p = v.pos()
            return -math.hypot(p[0] - self.arg[0], p[2] - self.arg[1])
        return _resolve(self.arg)(g, v)


def _resolve(spec):
    mod, fn = spec.split(':')
    __import__(mod)
    return getattr(sys.modules[mod], fn)


_W = {}


def _winit(mem, base, version, setup, paths):
    sys.path[:0] = [HERE] + list(paths)
    if setup:
        fn, args = setup
        g = _resolve(fn)(*args)
    elif isinstance(mem, tuple) and mem and mem[0] == 'b3313':
        sys.path.insert(0, mem[2])
        import b3313hl
        g = b3313hl.Game()
        g.run([tuple(x) for x in mem[1]])
    else:
        from sm64hl import Game
        g = Game(version=version)
        if g.base != base:
            raise RuntimeError('worker DLL base differs from the parent: raw state cannot cross processes')
        g.load((mem, ()))
    _W['g'], _W['root'], _W['cache'], _W['v'] = g, g.save(), {}, View(g)


def _state_for(g, prefix):
    c = _W['cache']
    if prefix not in c:
        if len(c) > 8:
            c.clear()
        g.load(_W['root'])
        for f in prefix:
            g.step(f)
        c[prefix] = g.save()
    return c[prefix]


def _trials(g, v, S, cands, cond, score, best):
    out = []
    for f in cands:
        g.load(S)
        g.step(f)
        ok = bool(cond(g, v))
        sc = best if ok else score
        out.append((f, ok, float(sc(g, v)) if sc is not None else 0.0, b'' if ok else ctypes.string_at(v.ms, 0xC8)))
    return out


def _wtask(job):
    prefix, cands, cond, score, best = job
    g = _W['g']
    return prefix, _trials(g, _W['v'], _state_for(g, prefix), cands, cond, score, best)


def _run_local(g, root, job):
    prefix, cands, cond, score, best = job
    g.load(root)
    for f in prefix:
        g.step(f)
    return prefix, _trials(g, View(g), g.save(), cands, cond, score, best)


def repair(g, target, frames=3, tol=None, buttons=None, sticks=None, beam=32, procs=0, **kw):
    frames = max(1, int(frames))
    return solve(g, Cond('near', (target, tol or TOL)), frames=frames,
                 buttons=buttons or [list(BUTTONS)] * frames, sticks=sticks or ['coarse'] * frames, beam=beam,
                 score=Score('near', target), best=Score('near', target), procs=procs, **kw)


def solve(g, condition, frames=1, buttons=None, sticks=None, beam=64, score=None, procs=None, dedupe='intended',
          chunk=2048, best=None, setup=None):
    t0 = time.time()
    root = g.save()
    frames = max(1, int(frames))
    buttons = buttons or [list(BUTTONS)] * frames
    if isinstance(buttons[0], str):
        buttons = [list(buttons)] * frames
    sticks = sticks or (['coarse'] * (frames - 1) + ['all'])
    procs = 0 if procs is None else procs
    pool = None
    if procs:
        if getattr(g, 'n64', False):
            mem = ('b3313', list(root.history), os.path.dirname(sys.modules[type(g).__module__].__file__))
        else:
            mem = None if setup else root[0]
        pool = mp.get_context('spawn').Pool(procs, _winit, (mem, g.base, g.version, setup,
                                                            [p for p in sys.path if p]))
    try:
        return _search(g, root, pool, condition, frames, buttons, sticks, beam, score, best, dedupe, chunk, t0)
    except Exception as ex:
        if pool is None:
            raise
        pool.terminate()
        pool = None
        r = _search(g, root, None, condition, frames, buttons, sticks, beam, score, best, dedupe, chunk, t0)
        r['fallback'] = f'{type(ex).__name__}: {ex}'
        return r
    finally:
        if pool:
            pool.terminate()
        g.load(root)


def _search(g, root, pool, condition, frames, buttons, sticks, beam, score, best, dedupe, chunk, t0):
    if True:
        layer, tried = [()], 0
        for k in range(frames):
            st = sticks[k]
            sl = (stick_classes(g, dedupe) if st == 'all' else coarse_sticks() if st == 'coarse' else list(st))
            cands = [(x, y, hi_byte(b), 0) for b in buttons[k] for (x, y) in sl]
            jobs = [(p, cands[i:i + chunk], condition, score, best) for p in layer for i in range(0, len(cands), chunk)]
            res = pool.imap_unordered(_wtask, jobs) if pool else (_run_local(g, root, j) for j in jobs)
            hits, nxt, seen = [], [], set()
            for prefix, out in res:
                tried += len(out)
                for f, ok, sc, sig in out:
                    if ok:
                        hits.append((sc, prefix + (f,)))
                    elif sig not in seen:
                        seen.add(sig)
                        nxt.append((sc, prefix + (f,)))
            if hits:
                hits.sort(key=lambda h: -h[0])
                inputs = list(hits[0][1])
                g.load(root)
                for f in inputs:
                    g.step(f)
                verified = bool(condition(g, View(g)))
                g.load(root)
                return {'found': True, 'inputs': inputs, 'frame': k + 1, 'tried': tried, 'solutions': len(hits),
                        'seconds': round(time.time() - t0, 1), 'verified': verified}
            nxt.sort(key=lambda h: -h[0])
            layer = [p for _, p in nxt[:beam]]
        g.load(root)
        return {'found': False, 'inputs': None, 'frame': None, 'tried': tried, 'seconds': round(time.time() - t0, 1),
                'verified': False}
