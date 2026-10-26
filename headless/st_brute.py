import argparse
import ctypes
import json
import math
import os
from quasi import Quasi
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STICK_CACHE = os.path.join(HERE, '_stick_exact.npz')
BTN_A, BTN_B, BTN_Z = 0x80, 0x40, 0x20


class Stick:

    def __init__(self, g):
        if os.path.exists(STICK_CACHE):
            d = np.load(STICK_CACHE)
            self.rel, self.mag = d['rel'], d['mag']
        else:
            self.rel, self.mag = self._build(g)
            np.savez(STICK_CACHE, rel=self.rel, mag=self.mag)
        by_rel = {}
        for x in range(-128, 128):
            for y in range(-128, 128):
                m = float(self.mag[x + 128, y + 128])
                if m > 0:
                    by_rel.setdefault(int(self.rel[x + 128, y + 128]) & 0xFFFF, []).append((m, x, y))
        self.rels = sorted(by_rel)
        self.by_rel = {r: sorted(v) for r, v in by_rel.items()}
        self.mags = {r: [m for m, _, _ in v] for r, v in self.by_rel.items()}

    @staticmethod
    def _build(g):
        import n64mem as NM
        t = NM.find_type('Controller')
        cm = {m[3]: m[1] for m in NM.members(t)}
        buf = ctypes.create_string_buffer(NM.layout(t)[2] + 64)
        adj = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(g.base + g.syms['adjust_analog_stick'])
        atan2s = ctypes.CFUNCTYPE(ctypes.c_short, ctypes.c_float, ctypes.c_float)(g.base + g.syms['atan2s'])
        rel = np.zeros((256, 256), np.int32)
        mag = np.zeros((256, 256), np.float32)
        a = ctypes.addressof(buf)
        for x in range(-128, 128):
            for y in range(-128, 128):
                ctypes.memmove(a + cm['rawStickX'], struct.pack('<h', x), 2)
                ctypes.memmove(a + cm['rawStickY'], struct.pack('<h', y), 2)
                adj(a)
                sx = struct.unpack('<f', ctypes.string_at(a + cm['stickX'], 4))[0]
                sy = struct.unpack('<f', ctypes.string_at(a + cm['stickY'], 4))[0]
                sm = struct.unpack('<f', ctypes.string_at(a + cm['stickMag'], 4))[0]
                m = (sm / 64.0) * (sm / 64.0) * 64.0 / 2.0
                mag[x + 128, y + 128] = m
                rel[x + 128, y + 128] = atan2s(-sy, sx) if m > 0 else 0
        return rel, mag

    def intent(self, x, y):
        return int(self.rel[x + 128, y + 128]) & 0xFFFF, float(self.mag[x + 128, y + 128])

    def raw(self, rel_yaw, mag):
        import bisect
        r = rel_yaw & 0xFFFF
        rels, n = self.rels, len(self.rels)
        j = bisect.bisect_left(rels, r)
        best = None
        for rr in (rels[(j - 1) % n], rels[j % n], rels[(j + 1) % n]):
            d = abs(((rr - r + 0x8000) & 0xFFFF) - 0x8000) * 64
            ms, lst = self.mags[rr], self.by_rel[rr]
            k = bisect.bisect_left(ms, mag)
            for kk in (k - 1, k):
                if 0 <= kk < len(lst):
                    m, x, y = lst[kk]
                    c = (d + abs(m - mag), x, y)
                    if best is None or c < best:
                        best = c
        return best[1], best[2]


class Search:
    def __init__(self, a, wid):
        import n64mem as NM
        import st_load as L
        from sm64hl import Game, read_m64
        self.a, self.wid = a, wid
        self.rng = Quasi(a.seed * 1000 + wid)           # a Halton sweep, no random draw
        self.g = g = Game()
        self.rep = L.load_st(g, a.start, romhack=a.romhack, rom=a.rom, log=lambda *x: None)
        g.history = []
        self.S0 = g.save()
        self.ms = g.addr('gMarioStates')
        self.M = {m[3]: m[1] for m in NM.members(NM.find_type('MarioState'))}
        self.off_action = self.M['action']
        self.cam_off = {m[3]: m[1] for m in NM.members(NM.find_type('Camera'))}['yaw']
        self.stick = Stick(g)
        self.macro_runner = None
        if 'macro' in a.ops:
            import macros as MC
            self.MC = MC
            self.macro_runner = MC.Runner(g, self.stick.raw)
            self.macro_names = (MC.task6_names() if a.macros == 'all' else
                                [MC.resolve(m) for m in a.macros.split(',') if m])
            bad = [m for m in self.macro_names if m not in MC.SPECS]
            if bad:
                raise SystemExit(f'--macros: unknown {bad} (the bank: {", ".join(MC.SPECS)})')
            self.macro_params = a.macro_params
        self.ref = [tuple(f) for f in read_m64(a.ref)]
        self.goal, self.mask = a.goal_action, a.goal_mask
        self.K = a.save_every
        self.set_best(self.ref, verify=False)
        if self.T is None:
            raise SystemExit(f'the reference movie never reaches action 0x{self.goal:X}')
        if a.resume:
            self.sync()

    def action(self):
        return self.g.read(self.ms, 'I', self.off_action)

    def cam_yaw(self):
        c = self.g.read('gCamera', 'Q')
        return self.g.read(c, 'h', self.cam_off) & 0xFFFF if c else 0

    def at_goal(self):
        if self.goal >= 0 and (self.action() & self.mask) != (self.goal & self.mask):
            return False
        a, g, ms, M = self.a, self.g, self.ms, self.M
        if a.goal_min_coins is not None and g.read(ms, 'h', M['numCoins']) < a.goal_min_coins:
            return False
        if a.goal_near is not None:
            x, y, z, r = a.goal_near
            p = [g.read(ms, 'f', M['pos'] + 4 * k) for k in range(3)]
            if (p[0] - x) ** 2 + (p[1] - y) ** 2 + (p[2] - z) ** 2 > r * r:
                return False
        return True

    def play(self, inputs, limit, keep_states=True):
        g = self.g
        g.load(self.S0)
        rec, states = [], {0: g.save()} if keep_states else {}
        for i, f in enumerate(inputs[:limit]):
            rel, mag = self.stick.intent(f[0], f[1])
            rec.append((None if mag == 0 else (rel + self.cam_yaw()) & 0xFFFF, mag, f[2], f[3], f[0], f[1], 0))
            g.step(f)
            if self.at_goal():
                return i + 1, rec, states
            if keep_states and (i + 1) % self.K == 0:
                states[i + 1] = g.save()
        return None, rec, states

    def set_best(self, inputs, verify=True):
        if verify and self.play(inputs, len(inputs), keep_states=False)[0] is None:
            return False
        self.states = None
        T, rec, states = self.play(inputs, len(inputs))
        self.best = [tuple(f) for f in inputs[:T]] if T else list(inputs)
        self.T, self.rec, self.states = T, rec, states
        return True

    def mutate(self):
        a, R = self.a, self.rng
        lo, hi = a.r_range
        r = R.randrange(max(1, int(lo * self.T)), max(2, int(hi * self.T)))
        rows = [list(x) for x in self.rec[r:]]
        n_ops = R.choice((1, 1, 1, 2, 3))
        for _ in range(n_ops):
            op = R.choice(a.ops)
            if not rows:
                break
            if op == 'delete':
                k = R.randint(1, 4)
                i = R.randrange(0, min(8, len(rows)))
                del rows[i:i + k]
            elif op == 'stick':
                w = R.randint(1, 10)
                d = int(math.copysign(2 ** R.uniform(4, 12), R.random() - 0.5))
                full = R.random() < 0.3
                base = next((rw[0] for rw in rows[:w] if rw[0] is not None and rw[0] != 'MACRO'), None)
                for rw in rows[:w]:
                    if rw[0] == 'MACRO':
                        continue
                    if rw[0] is None:
                        if base is None:
                            continue
                        rw[0], rw[1] = base, 32.0
                    rw[0] = (rw[0] + d) & 0xFFFF
                    rw[6] = 1
                    if full:
                        rw[1] = 32.0
            elif op == 'button':
                i = R.randrange(0, min(10, len(rows)))
                if rows[i][0] == 'MACRO' or (i + 1 < len(rows) and rows[i + 1][0] == 'MACRO'):
                    continue
                b = R.choice((BTN_A, BTN_B, BTN_Z))
                if R.random() < 0.5 or i + 1 >= len(rows):
                    rows[i][2] ^= b
                else:
                    j = i + 1 if R.random() < 0.5 else max(0, i - 1)
                    bi, bj = rows[i][2] & b, rows[j][2] & b
                    rows[i][2] = (rows[i][2] & ~b) | bj
                    rows[j][2] = (rows[j][2] & ~b) | bi
            elif op == 'segment':
                w = R.randint(20, 120)
                amp = math.copysign(2 ** R.uniform(7, 12.5), R.random() - 0.5)
                for t, rw in enumerate(rows[:w]):
                    if rw[0] is None or rw[0] == 'MACRO':
                        continue
                    rw[0] = (rw[0] + int(amp * math.sin(math.pi * (t + 0.5) / w))) & 0xFFFF
                    rw[6] = 1
            elif op == 'macro':
                i = R.randrange(0, min(10, len(rows)))
                base = next((rw[0] for rw in rows[i:] if rw[0] is not None and rw[0] != 'MACRO'), None)
                if base is None:
                    continue
                yaw = (base + int(R.gauss(0, 2048))) & 0xFFFF
                name = R.choice(self.macro_names)
                if self.macro_params == 'sample':
                    k = self.MC.sample_params(name, R)
                else:
                    k = R.randint(1, 3) if name.startswith('dive') else R.randint(3, 14)
                rows.insert(i, ['MACRO', name, yaw, k, 0, 0, 1])
            elif op == 'swap':
                i = R.randrange(0, max(1, min(10, len(rows) - 1)))
                if i + 1 < len(rows):
                    rows[i], rows[i + 1] = rows[i + 1], rows[i]
        return r, rows

    def evaluate(self, r, rows, tail, limit):
        g = self.g
        k = max(s for s in self.states if s <= r)
        g.load(self.states[k])
        for f in self.best[k:r]:
            g.step(f)
        raw = list(self.best[:r])
        skip = 0
        for yaw, mag, hi, lo, x0, y0, modified in rows:
            if len(raw) >= limit:
                return None, raw
            if yaw == 'MACRO':
                played = (self.MC.run_named(self.macro_runner, mag, hi, params=lo) if isinstance(lo, dict)
                          else self.MC.run_named(self.macro_runner, mag, hi, lo))
                raw.extend(played)
                skip = len(played)
                if self.at_goal():
                    return len(raw), raw
                continue
            if skip:
                skip -= 1
                continue
            if yaw is None:
                x = y = 0
            elif tail == 'raw' and not modified:
                x, y = x0, y0
            else:
                x, y = self.stick.raw((yaw - self.cam_yaw()) & 0xFFFF, mag)
            f = (x, y, hi & 0xFF, lo & 0xFF)
            g.step(f)
            raw.append(f)
            if self.at_goal():
                return len(raw), raw
        return None, raw

    def sync(self):
        p = os.path.join(self.a.out, 'best.json')
        try:
            with open(p) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return
        if d['T'] < self.T:
            if self.set_best([tuple(f) for f in d['inputs']]):
                self.log(f'adopted shared best T {self.T}')

    def publish(self):
        from sm64hl import write_m64
        a = self.a
        p = os.path.join(a.out, 'best.json')
        try:
            cur = json.load(open(p))['T']
        except (OSError, ValueError, KeyError):
            cur = None
        stem = os.path.join(a.out, f'T{self.T}_w{self.wid}')
        write_m64(self.best, stem + '.m64', poweron=False, template=a.ref)
        rec = {'T': self.T, 'goal_action': hex(self.goal), 'start': os.path.abspath(a.start), 'romhack': a.romhack, 'rom': a.rom and os.path.abspath(a.rom), 'goal_min_coins': a.goal_min_coins, 'goal_near': a.goal_near,
               'ref': os.path.abspath(a.ref), 'worker': self.wid, 'inputs': [list(f) for f in self.best],
               'time': time.strftime('%Y-%m-%d %H:%M:%S')}
        json.dump(rec, open(stem + '.json', 'w'))
        if cur is None or self.T < cur:
            tmp = p + f'.{os.getpid()}.tmp'
            json.dump(rec, open(tmp, 'w'))
            for k in range(50):
                try:
                    os.replace(tmp, p)
                    break
                except PermissionError:
                    time.sleep(0.1)

    def log(self, msg):
        print(f'[w{self.wid} {time.strftime("%H:%M:%S")}] {msg}', flush=True)

    def run(self, deadline):
        a = self.a
        n = acc = 0
        t_sync = time.time()
        self.log(f'start T {self.T} (reference), ops {a.ops}, tail {a.tail}')
        while time.time() < deadline:
            n += 1
            r, rows = self.mutate()
            tail = self.rng.choice(('intent', 'raw')) if a.tail == 'mix' else a.tail
            eq = self.rng.random() < a.accept_equal
            T, raw = self.evaluate(r, rows, tail, self.T if eq else self.T - 1)
            if T is not None and (T < self.T or (eq and T == self.T and raw != self.best)):
                old = self.T
                if not self.set_best(raw):
                    self.log('WARNING: accepted candidate did not replay from the .st (non-deterministic?)')
                    continue
                acc += 1
                if self.T < old:
                    self.log(f'IMPROVEMENT T {old} -> {self.T} ({tail} tail, row {r}, {n} candidates)')
                    self.publish()
            if time.time() - t_sync > a.sync:
                t_sync = time.time()
                self.sync()
                self.log(f'{n} candidates, {acc} accepted, best T {self.T}, {_rss_mb()} MB')


def _rss_mb():
    try:
        import psutil
        return int(psutil.Process().memory_info().rss / 2 ** 20)
    except Exception:
        return '?'


def worker(a, wid, deadline):
    s = Search(a, wid)
    s.run(deadline)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--start', required=True, help='Mupen .st to start from')
    ap.add_argument('--ref', required=True, help='reference .m64 (inputs from the .st)')
    ap.add_argument('--goal-action', type=lambda s: int(s, 0), default=0x1302, help='goal action (default 0x1302 star dance); -1 = no action condition (with --goal-min-coins / --goal-near)')
    ap.add_argument('--goal-mask', type=lambda s: int(s, 0), default=0xFFFFFFFF, help='mask applied to the action before the comparison')
    ap.add_argument('--goal-min-coins', type=int, default=None, help='the goal also needs at least this many coins (e.g. a red-coin star, not another star of the level)')
    ap.add_argument('--goal-near', type=lambda s: tuple(float(x) for x in s.split(',')), default=None, help='x,y,z,r: the goal also needs Mario within r of (x, y, z)')
    ap.add_argument('--romhack', action='store_true', help='load the .st with st_load romhack=True')
    ap.add_argument('--rom', default=None, help='with --romhack: ROM of the hack (.z64): its levels are installed, so warps / level changes in the movie are reproduced')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--time', type=float, default=3600, help='seconds')
    ap.add_argument('--out', default='brute_out')
    ap.add_argument('--ops', default='delete,stick,button,swap', help='mutations to use (comma list); macro = insert a closed-loop technique of macros.py (see --macros), off by default')
    ap.add_argument('--macros', default='dive_rollout,dive_kick,wall_kick,side_flip', help='techniques the macro mutation draws from: names of the macros.py bank (aliases accepted, see MOVESET.md), or all = every macro allowed in Task 6')
    ap.add_argument('--macro-params', choices=['k', 'sample'], default='k', help='params of an inserted macro: k = the first API (one k = cycles or hold frames); sample = every param of the macro drawn in its range (macros.sample_params: hold / wait / land / cycles / stick mag and angle), the in-place sweep')
    ap.add_argument('--tail', choices=('intent', 'raw', 'mix'), default='mix')
    ap.add_argument('--accept-equal', type=float, default=0.3, help='probability to accept a different movie with the same T (0 = off)')
    ap.add_argument('--save-every', type=int, default=40, help='rows between saved states of the best movie (a state is ~10 MB per worker)')
    ap.add_argument('--sync', type=float, default=30, help='seconds between reads of the shared best')
    ap.add_argument('--r-range', type=lambda s: tuple(float(x) for x in s.split(',')), default=(0.0, 1.0),
                    help='mutations at rows in [lo*T, hi*T) (fractions, e.g. 0,0.5 = first half)')
    ap.add_argument('--resume', action='store_true', help='start from --out/best.json when it beats the reference')
    ap.add_argument('--seed', type=int, default=1)
    return ap


def parse(argv=None):
    a = build_parser().parse_args(argv)
    a.ops = [o for o in a.ops.split(',') if o]
    return a


def main():
    a = parse()
    os.makedirs(a.out, exist_ok=True)
    deadline = time.time() + a.time
    if a.workers == 1:
        worker(a, 0, deadline)
        return
    import multiprocessing as mp
    ps = [mp.Process(target=worker, args=(a, i, deadline)) for i in range(a.workers)]
    for p in ps:
        p.start()
    try:
        while any(p.is_alive() for p in ps) and time.time() < deadline + 60:
            time.sleep(5)
    finally:
        for p in ps:
            if p.is_alive():
                p.terminate()
    try:
        d = json.load(open(os.path.join(a.out, 'best.json')))
        print(f'best T {d["T"]} (worker {d["worker"]}) -> {a.out}')
    except (OSError, ValueError):
        print('no improvement')


if __name__ == '__main__':
    main()
