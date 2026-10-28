"""An AI plays Super Mario 64 headless: an MCP server (stdio, JSON-RPC 2.0) on this repo's engine.

No emulator: the decompiled game compiled natively (sm64_us.dll / sm64_jp.dll, unlocked with your own ROM by
INSTALL.bat), 35,000-55,000 frames/s a core, bit-exact with Mupen64. The AI reads everything straight from RAM (Mario,
the objects by behaviour name, the floors and walls), plays raw inputs, walks toward points with the camera handled,
plays the engine's moves (dive rollout, long jumps, slide kick, triple jump, wall kick, BLJ...), tries a move at
dozens of facings before it commits, keeps checkpoints, and exports its run as a .m64 to replay in Mupen.

    claude mcp add sm64-headless -- python <repo>/agent/sm64_headless_mcp.py      (INSTALL.bat does it)
    python agent/sm64_headless_mcp.py --demo [GAME.st]                            (a few calls, no MCP client)
"""
import json
import math
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, 'headless'), os.path.join(ROOT, 'tas_lib')):
    sys.path.insert(0, p)

BUTTONS = {'A': (0, 0x80), 'B': (0, 0x40), 'Z': (0, 0x20), 'START': (0, 0x10), 'R': (1, 0x10), 'L': (1, 0x20),
           'CU': (1, 0x08), 'CD': (1, 0x04), 'CL': (1, 0x02), 'CR': (1, 0x01)}


def buttons_of(names):
    hi = lo = 0
    for n in names or ():
        k, b = BUTTONS[str(n).upper()]
        if k == 0:
            hi |= b
        else:
            lo |= b
    return hi, lo


WHY = {'arrive': 'arrived', 'bloque': 'a dialog or a cutscene holds the game (close_dialog)',
       'brule': 'burnt (lava or fire)', 'touche': 'hit by an enemy', 'chute': 'fell (out of the level\'s floor)',
       'niveau': 'the level changed', 'max': 'max'}


def _seg_dist(px, pz, p, q):
    """the distance on the ground (x, z) from a point to a triangle's edge"""
    ex, ez = q[0] - p[0], q[2] - p[2]
    n = ex * ex + ez * ez
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((px - p[0]) * ex + (pz - p[2]) * ez) / n))
    return math.hypot(px - p[0] - t * ex, pz - p[2] - t * ez)


class Play:
    """the AI's game: one headless game, its run as a stack of states (undo / checkpoints / export)"""

    def __init__(self):
        self.g = None

    def need(self):
        if self.g is None:
            raise RuntimeError('call start first')
        return self.g

    # ---- the run's history -----------------------------------------------------------------------------------
    def _push(self, label):
        g = self.g
        self.stack.append((g.save(), len(g.history), label))

    def _back_to(self, n):
        while len(self.stack) > n:
            self.stack.pop()
        state, h = (self.stack[-1][:2] if self.stack else self.root)
        self.g.load(state)
        del self.g.history[h:]

    def _frames(self):
        return len(self.g.history) - self.root[1]

    def state(self):
        g = self.need()
        m = g.mario()
        out = {k: (round(float(v), 2) if isinstance(v, float) else v) for k, v in m.items()
               if k in ('pos_x', 'pos_y', 'pos_z', 'forward_vel', 'face_yaw', 'action', 'level', 'area', 'cam_yaw',
                        'vel_y', 'intended_yaw', 'intended_mag')}
        out['action_name'] = self.M.NAME.get(m['action'], hex(m['action']))
        out['face_yaw_deg'] = round((int(m['face_yaw']) & 0xFFFF) * 360.0 / 65536.0, 1)     # (0 = +z, 90 = +x)
        if self.swimmer().in_water():
            out['in_water'] = True
            out['water_level'] = self.swimmer().read()['water']
        out['coins'], out['stars'] = g.coins, g.stars
        out['health'] = (g.health >> 8) & 0xFF
        out['frames'] = self._frames()
        try:
            import hlx
            if hlx.blocked(g):
                out['blocked'] = ('a dialog, the pause menu or a cutscene holds the game: call close_dialog (it presses A '
                                  'until it is over)')
        except Exception:
            pass
        return out

    def swimmer(self):
        if getattr(self, '_sw', None) is None or self._sw.g is not self.g:
            import swim
            self._sw = swim.Swimmer(self.g)
        return self._sw

    def aim(self, a):
        """the world yaw a call aims at: toward (x, z), a yaw in degrees (0 = +z, 90 = +x), or Mario's facing"""
        m = self.g.mario()
        if a.get('x') is not None and a.get('z') is not None:
            return int(round(math.atan2(float(a['x']) - m['pos_x'], float(a['z']) - m['pos_z']) * 32768.0 / math.pi)) & 0xFFFF
        if a.get('yaw_deg') is not None:
            return int(round(float(a['yaw_deg']) * 65536.0 / 360.0)) & 0xFFFF
        return int(m['face_yaw']) & 0xFFFF

    # ---- the tools ----------------------------------------------------------------------------------------------
    def t_start(self, a):
        """a game: from a Mupen savestate (st), a movie / input file (movie: .m64 / .hls / .json, from power-on), else
        Mario standing in the castle grounds (US; power_on=true: the console just switched on); version us / jp"""
        import macros as M
        import sm64hl
        import st_brute
        t0 = time.time()
        g = sm64hl.Game(version=str(a.get('version', 'us')).lower())
        self.start_st = None
        if a.get('st'):
            import st_load
            st_load.load_st(g, a['st'])
            self.start_st = os.path.abspath(a['st'])
        elif a.get('movie'):
            g.load_file(a['movie'])
        elif not a.get('power_on') and str(a.get('version', 'us')).lower() == 'us':
            g.load_file(os.path.join(ROOT, 'headless', 'start_grounds.hls'))     # (Mario standing in the castle grounds)
        self.g, self.M = g, M
        self.run = M.Runner(g, st_brute.Stick(g).raw)
        self.root = (g.save(), len(g.history))
        self.stack, self.marks = [], {}
        out = self.state()
        out['opened_in_s'] = round(time.time() - t0, 1)
        return out

    def t_state(self, a):
        return self.state()

    def t_scan(self, a):
        g = self.need()
        radius = float(a.get('radius', 3000))
        flt = str(a.get('filter') or '').lower()
        m = g.mario()
        px, py, pz = m['pos_x'], m['pos_y'], m['pos_z']
        objs = []
        for o in g.objects():
            if o['bhv'] == 'bhvMario':
                continue
            d = math.hypot(o['pos_x'] - px, o['pos_z'] - pz)
            if flt and flt not in str(o['bhv']).lower():
                continue
            if not flt and d > radius:
                continue
            objs.append({'name': o['bhv'], 'x': round(o['pos_x']), 'y': round(o['pos_y']), 'z': round(o['pos_z']),
                         'dist': round(d), 'dy': round(o['pos_y'] - py), 'slot': o['slot']})
        objs.sort(key=lambda o: o['dist'])
        out = {'mario': self.state(), 'objects': objs[:int(a.get('limit', 40))]}
        if a.get('geometry', True):
            floors, walls = {}, []
            r2 = min(radius, 2000.0) ** 2
            for s in g.surfaces():
                v = (s['v1'], s['v2'], s['v3'])
                cx, cy, cz = (sum(p[0] for p in v) / 3, sum(p[1] for p in v) / 3, sum(p[2] for p in v) / 3)
                d = min(_seg_dist(px, pz, v[i], v[(i + 1) % 3]) for i in range(3))     # (the nearest edge, not the middle)
                if d * d > r2:
                    continue
                if s['kind'] == 'floor':
                    h = int(round(cy / 50.0) * 50)
                    if h not in floors or d < floors[h]['dist']:
                        floors[h] = {'height': h, 'dh': h - round(py), 'dist': round(d), 'x': round(cx), 'z': round(cz)}
                elif s['kind'] == 'wall':
                    walls.append({'x': round(cx), 'y': round(cy), 'z': round(cz), 'dist': round(d),
                                  'nx': round(s['normal'][0], 2), 'nz': round(s['normal'][2], 2)})
            walls.sort(key=lambda w: w['dist'])
            out['floors'] = sorted(floors.values(), key=lambda f: f['dist'])[:20]
            out['walls'] = walls[:15]
        return out

    def t_step(self, a):
        g = self.need()
        if a.get('inputs'):
            inp = [tuple(int(v) for v in (list(f) + [0, 0, 0, 0])[:4]) for f in a['inputs']]
        else:
            hi, lo = buttons_of(a.get('buttons'))
            inp = [(int(a.get('stick_x', 0)), int(a.get('stick_y', 0)), hi, lo)] * max(1, int(a.get('frames', 1)))
        for f in inp:
            g.step(f)
        self._push('step')
        return self.state()

    def t_walk(self, a):
        self.need()
        hi, _ = buttons_of(a.get('buttons'))
        mag = float(a.get('mag', 32.0))
        for _ in range(max(1, int(a.get('frames', 10)))):
            self.run.press(self.aim(a), hi, mag)
        self._push('walk')
        return self.state()

    def t_move(self, a):
        g = self.need()
        name = self.M.resolve(a['name'])
        if name not in self.M.SPECS:
            raise RuntimeError('no move named %s: moves() lists them' % a['name'])
        spec = self.M.SPECS[name]
        m0 = g.mario()
        a0 = self.M.NAME.get(m0['action'], hex(m0['action']))
        ok, why0 = self.M.in_domain(name, m0['action'], m0['forward_vel'], g)
        params = dict(a.get('params') or {})
        bad = [k for k in params if k not in spec.get('params', {})]
        if bad:
            raise RuntimeError('%s has no parameter %s (its parameters: %s)'
                               % (name, ', '.join(bad), spec.get('params') or 'none'))
        if a.get('k') is not None and not params:
            key = 'cycles' if 'cycles' in spec.get('params', {}) else 'hold'
            if key in spec.get('params', {}):
                params[key] = max(1, int(a['k'])) if key == 'cycles' else max(0, min(20, int(a['k'])))
        n0 = len(self.stack)
        inp, status = self.run.run(name, self.aim(a), **params)
        self._push('move ' + name)
        out = self.state()
        out['frames_played'] = len(inp)
        if (status in ('fail', 'locked') and len(inp) <= 2) or not inp:
            why = why0 if not ok else (getattr(self.run, 'why', '') or 'the game did not take it')
            if a.get('keep_failed'):
                out['warning'] = '%s refused from %s: %s' % (name, a0, why)
                return out
            self._back_to(n0)               # (B while walking under speed 29 punches instead of diving: never kept)
            out = self.state()
            out.update(kept=False, frames_played=0,
                       warning=('%s refused from %s (after %d frame(s) it was %s): %s. Nothing was kept; moves() '
                                'says what it needs (require)' % (name, a0, len(inp), out['action_name'], why)))
        elif status == 'fail':
            out['warning'] = 'played %d frames but did not end as planned: %s' % (len(inp), getattr(self.run, 'why', ''))
        return out

    def t_moves(self, a):
        """every move: what it does, its parameters (frame-perfect waits, holds, cycles) and what it needs"""
        import macros as M
        out = []
        for n in sorted(M.SPECS):
            sp = M.SPECS[n]
            out.append({'name': n, 'doc': sp.get('doc', ''), 'params': sp.get('params', {}),
                        'require': sp.get('require', {}),
                        'aliases': sorted(k for k, v in M.ALIASES.items() if v == n)})
        return {'moves': out,
                'water': 'in the water the moves do not apply: swim(x, z, y) swims with the fastest strokes'}

    def t_try(self, a):
        n0 = len(self.need() and self.stack)
        r = self.t_move(a) if a.get('name') else self.t_walk(a)
        self._back_to(n0)
        r['kept'] = False
        return r

    def t_best(self, a):
        g = self.need()
        n0 = len(self.stack)
        tx, tz = a.get('x'), a.get('z')
        if tx is not None and tz is not None:
            base = self.aim({'x': tx, 'z': tz}) * 360.0 / 65536.0
        else:
            base = float(a.get('yaw_deg', int(g.mario()['face_yaw']) * 360.0 / 65536.0))
        spread, step = float(a.get('spread', 45.0)), max(0.5, float(a.get('step', 5.0)))
        res, k = [], -spread
        while k <= spread + 1e-6:
            b = dict(a, yaw_deg=(base + k) % 360.0)
            b.pop('x', None)
            b.pop('z', None)
            r = self.t_move(b) if a.get('name') else self.t_walk(b)
            d = math.hypot(r['pos_x'] - float(tx), r['pos_z'] - float(tz)) if tx is not None and tz is not None else None
            res.append({'yaw_deg': round((base + k) % 360.0, 1), 'x': r['pos_x'], 'y': r['pos_y'], 'z': r['pos_z'],
                        'forward_vel': r['forward_vel'], 'action': r['action_name'],
                        'frames': r.get('frames_played', int(a.get('frames', 10))),
                        'dist_left': None if d is None else round(d, 1), 'warning': r.get('warning')})
            self._back_to(n0)
            k += step
        res.sort(key=lambda q: (q['warning'] is not None,
                                q['dist_left'] if q['dist_left'] is not None else -q['forward_vel']))
        out = {'tried': len(res), 'best': res[:5]}
        if a.get('commit') and res and res[0]['warning']:
            out['kept'] = None
            out['why_not_kept'] = 'the game refused the move at every facing: ' + res[0]['warning']
        elif a.get('commit') and res:
            b = dict(a, yaw_deg=res[0]['yaw_deg'])
            b.pop('x', None)
            b.pop('z', None)
            out['kept'] = self.t_move(b) if a.get('name') else self.t_walk(b)
        return out

    def t_swim(self, a):
        """swim to (x, z) (y: a depth to reach, else level) with the fastest strokes; out by a water jump when the
        target is above the water, when exit=true, or when the shore blocks Mario at the surface"""
        g = self.need()
        import swim
        tx, tz = float(a['x']), float(a['z'])
        ty = float(a['y']) if a.get('y') is not None else None
        n = len(g.history)
        ins, why = swim.swim_to(g, tx, tz, ty=ty, stop=float(a.get('stop', 100)), maxf=int(a.get('frames', 900)),
                                exit_out=a.get('exit'))
        if ins:
            self._push('swim')
        m = g.mario()
        out = {'reached': why == 'arrived', 'why': why, 'frames': len(g.history) - n,
               'dist_left': round(math.hypot(tx - m['pos_x'], tz - m['pos_z']), 1), 'mario': self.state()}
        if why == 'not in water':
            out['hint'] = 'Mario is not in the water: goto or a move (jump, dive) gets him in first'
        elif why in ('jumped out', 'left the water'):
            out['hint'] = 'out of the water: goto / move go on from here'
        return out

    def _land(self, tx, tz):
        for _ in range(90):                 # (out of the water by a jump: on the ground first)
            if not self.g.mario()['action'] & 0x800:
                break
            self.run.press(self.aim({'x': tx, 'z': tz}), 0, 32.0)

    def t_goto(self, a):
        """to (x, z): style auto (chained long jumps when far, running when near, swimming in the water), walk,
        longjump or swim; enemies avoided"""
        g = self.need()
        import hlx
        tx, tz = float(a['x']), float(a['z'])
        style = str(a.get('style', 'auto'))
        stop = float(a.get('stop', 50))
        n, legs, why = len(g.history), [], ''
        import swim
        ty = float(a['y']) if a.get('y') is not None else None

        def swim_leg(legs_):
            ins_, w = swim.swim_to(g, tx, tz, ty=ty, stop=max(stop, 80.0), maxf=int(a.get('frames', 900)))
            legs_.append('swim: ' + w)
            if w in ('jumped out', 'left the water'):
                self._land(tx, tz)
            return w

        def land_leg(kind, legs_):
            wet = lambda g_: self.swimmer().in_water()
            if kind == 'longjump':
                _, w = hlx.go_lj(g, tx, tz, stop=max(stop, 80.0), maxf=int(a.get('frames', 400)), done=wet)
            else:
                self._land(tx, tz)
                left, w = int(a.get('frames', 300)), 'max'
                while left > 0 and not wet(g):
                    ins_, w = hlx.go_walk(g, tx, tz, stop=stop, maxf=min(10, left))
                    left -= max(1, len(ins_))
                    if w != 'max':
                        break
            w = 'in the water' if wet(g) else WHY.get(w, w)
            legs_.append('%s: %s' % (kind, w))
            if w == 'in the water' and style != kind:
                w = swim_leg(legs_)
            return w
        if self.swimmer().in_water() or style == 'swim':
            why = swim_leg(legs)
        m = g.mario()
        d = math.hypot(tx - m['pos_x'], tz - m['pos_z'])
        if style != 'swim' and why != 'arrived' and not self.swimmer().in_water() and d > stop:
            # auto: both ways raced from here (headless, a fraction of a second each), the faster arrival kept, else
            # the one that got nearer
            kinds = {'walk': ['walk'], 'longjump': ['longjump']}.get(style, ['longjump', 'walk'])
            s0, res = g.save(), []
            for kind in kinds:
                g.load(s0)
                lg = list(legs)
                w = land_leg(kind, lg)
                m = g.mario()
                res.append((w != 'arrived', len(g.history), math.hypot(tx - m['pos_x'], tz - m['pos_z']), kind, w, lg,
                            g.save()))
            res.sort(key=lambda r: (r[0], r[1] if not r[0] else r[2]))
            why, legs = res[0][4], res[0][5]
            g.load(res[0][6])
            if len(res) > 1:
                legs.append('raced: %s' % ', '.join('%s %s in %d f' % (r[3], 'arrived' if not r[0] else
                                                                        '%d left' % r[2], r[1] - n) for r in res))
        self._push('goto')
        m = g.mario()
        out = {'reached': why == 'arrived', 'why': why, 'frames': len(g.history) - n,
               'legs': legs, 'dist_left': round(math.hypot(tx - m['pos_x'], tz - m['pos_z']), 1), 'mario': self.state()}
        if not out['reached'] and why in ('max', 'in the water'):
            out['hint'] = ('not there: a wall, a slope or a gap may hold Mario; scan its walls and floors, go by a '
                           'nearer point, or use moves (a jump, a wall kick) to get past')
        return out

    def t_collect(self, a):
        """go and take an object (part of its behaviour name; near_x / near_z: the one nearest that point); then on,
        a jump, a ground pound if standing under it is not enough; collected only if a counter went up or it is gone"""
        g = self.need()
        flt = str(a['name']).lower()
        objs = [o for o in g.objects() if flt in str(o['bhv']).lower() and o['bhv'] != 'bhvMario']
        if not objs:
            return {'collected': False, 'why': 'no object named like %s' % a['name']}
        m = g.mario()
        nx = float(a['near_x']) if a.get('near_x') is not None else m['pos_x']
        nz = float(a['near_z']) if a.get('near_z') is not None else m['pos_z']
        o = min(objs, key=lambda q: math.hypot(q['pos_x'] - nx, q['pos_z'] - nz))
        c0, s0 = g.coins, g.stars

        def taken():
            gone = not any(q['slot'] == o['slot'] and q['bhv'] == o['bhv'] for q in g.objects())
            return gone or g.coins > c0 or g.stars > s0
        r = self.t_goto({'x': o['pos_x'], 'z': o['pos_z'], 'y': o['pos_y'], 'stop': 30, 'style': 'auto'})
        got = taken()
        if not got and self.swimmer().in_water():
            import swim
            swim.swim_to(g, o['pos_x'], o['pos_z'], ty=o['pos_y'], stop=20.0, maxf=120, exit_out=False)
            got = taken()
        for _ in range(90):                 # (a long jump can end in the air: land first, the object checked each frame)
            if got or not g.mario()['action'] & 0x800:
                break
            self.run.press(self.aim({'x': o['pos_x'], 'z': o['pos_z']}), 0, 32.0)
            got = taken()
        if not self.stack or self.stack[-1][1] != len(g.history):
            self._push('land')
        if not got:
            n0 = len(self.stack)
            tries = ([([], n) for n in (1, 2, 3, 4, 6, 8, 10, 14, 18, 24)] +
                     [(['A'], n) for n in (4, 6, 8, 10, 12, 15, 18, 22, 26, 30)] +
                     [(['Z'], n) for n in (2, 4, 6, 10, 15, 20)])
            for btn, n in sorted(tries, key=lambda t: t[1]):
                self.t_walk({'x': o['pos_x'], 'z': o['pos_z'], 'frames': n, 'buttons': btn})
                if taken():
                    got = True
                    break
                self._back_to(n0)
        return {'collected': bool(got), 'target': {'name': o['bhv'], 'x': round(o['pos_x']), 'y': round(o['pos_y']),
                                                   'z': round(o['pos_z'])},
                'coins': [c0, g.coins], 'stars': [s0, g.stars], 'goto': {k: r[k] for k in ('reached', 'why', 'frames')},
                'mario': self.state(), **({} if got else self._why_missed(o))}

    def _why_missed(self, o):
        m = self.g.mario()
        dy = o['pos_y'] - m['pos_y']
        d = math.hypot(o['pos_x'] - m['pos_x'], o['pos_z'] - m['pos_z'])
        if dy > 150:
            hint = ('the object is %d units above Mario: reach its platform first (scan its floors, goto / move a jump '
                    'onto it), then collect again' % dy)
        elif dy < -300:
            hint = 'the object is %d units below Mario: get down to its floor first' % -dy
        elif d > 100:
            hint = 'Mario stopped %d units away (a wall or a gap?): scan the walls, try another approach' % d
        else:
            hint = 'Mario is on it but nothing was taken: it may need an action (a punch, a ground pound) or be inactive'
        return {'dy': round(dy), 'dist': round(d), 'hint': hint}

    def t_close_dialog(self, a):
        g = self.need()
        import hlx
        n = len(g.history)
        hlx.close_dialog(g, maxf=int(a.get('frames', 400)))
        for _ in range(120):                # (the text box gone, Mario still ends his reading animation: neutral, no jump)
            if not hlx.blocked(g):
                break
            g.step((0, 0, 0, 0))
        self._push('close_dialog')
        out = self.state()
        out['frames_played'] = len(g.history) - n
        return out

    def t_checkpoint(self, a):
        self.need()
        self.marks[str(a.get('name', 'cp'))] = len(self.stack)
        return {'saved': str(a.get('name', 'cp')), 'frames': self._frames()}

    def t_restore(self, a):
        self.need()
        n = self.marks.get(str(a.get('name', 'cp')))
        if n is None:
            raise RuntimeError('no checkpoint named %s' % a.get('name', 'cp'))
        self._back_to(n)
        return self.state()

    def t_undo(self, a):
        self.need()
        self._back_to(max(0, len(self.stack) - max(1, int(a.get('n', 1)))))
        return self.state()

    def t_export(self, a):
        g = self.need()
        import sm64hl
        path = os.path.abspath(a['path'])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        inputs = list(g.history[self.root[1]:]) if self.start_st else list(g.history)
        sm64hl.write_m64(inputs, path, poweron=self.start_st is None)
        out = {'m64': path, 'frames': len(inputs)}
        if self.start_st:                   # (the movie starts from the savestate it was played from)
            st = os.path.splitext(path)[0] + '.st'
            if os.path.abspath(st) != self.start_st:
                shutil.copy(self.start_st, st)
            out['st'] = st
        return out

    def t_snapshot(self, a):
        """the game's whole state to a file (headless: loaded back at once, here or later with load_snapshot)"""
        g = self.need()
        path = os.path.abspath(a['path'])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        g.save_snapshot(path)
        return {'snapshot': path, 'frames': self._frames()}


TOOLS_SPEC = [
    ('start', 'Open a game: st = a Mupen savestate, or movie = a .m64 / .hls played from power-on, or nothing: Mario '
              'standing in the castle grounds (US; power_on=true for the console just switched on); version us / jp. '
              'Call once (again to start over).',
     {'st': 'string', 'movie': 'string', 'version': 'string', 'power_on': 'boolean'}, []),
    ('state', "Mario: position (pos_x / pos_y / pos_z, units; y is up), forward_vel (units a frame; running caps at "
              "32, a long jump at 48), facing (face_yaw_deg: 0 = +z, 90 = +x; face_yaw the game's 0..65535), "
              "action_name, level / area, coins, stars, health (0..8), frames played (30 a second), in_water and "
              "water_level when swimming.", {}, []),
    ('scan', "Read from RAM: objects (behaviour name, position, distance, height above Mario), floors by height, "
             "walls. filter='coin' / 'star' / any part of a behaviour name finds every match in the level.",
     {'radius': 'number', 'filter': 'string', 'limit': 'number', 'geometry': 'boolean'}, []),
    ('step', 'Raw controller frames: stick_x / stick_y (-128..127), buttons (A B Z START R L CU CD CL CR), frames; or '
             'inputs = [[x, y, hi, lo], ...].',
     {'frames': 'number', 'stick_x': 'number', 'stick_y': 'number', 'buttons': 'array', 'inputs': 'array'}, []),
    ('walk', 'The stick toward (x, z) or a world direction yaw_deg (0 = +z, 90 = +x) for frames, the camera handled '
             'each frame; buttons held; mag 0..32.',
     {'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'frames': 'number', 'buttons': 'array', 'mag': 'number'}, []),
    ('move', "One of the engine's speedrun moves, frame perfect and closed loop (moves() lists them with their docs: "
             "dive_rollout, lj_chain, slide_kick, triple_jump, wall_kick, side_flip, blj...) aimed at (x, z) or yaw_deg. "
             "params: its frame-perfect settings ({'cycles': 3}, {'hold': 4, 'wait': 1}: moves() gives each move's); "
             "k: a shortcut for cycles / hold. A move the game refuses is taken back (kept=false) with the reason.",
     {'name': 'string', 'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'k': 'number', 'params': 'object',
      'keep_failed': 'boolean'}, ['name']),
    ('moves', "Every move: what it does (doc), its frame-perfect parameters with their defaults (params), what it "
              "needs to start (require: fv_min = the forward speed).", {}, []),
    ('try', 'A walk or a move (same arguments) played and taken back: what it would give.',
     {'name': 'string', 'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'frames': 'number', 'buttons': 'array',
      'mag': 'number', 'k': 'number', 'params': 'object'}, []),
    ('best', 'A move (name) or a walk (frames) tried at many facings at once (spread +- degrees around (x, z) or '
             'yaw_deg, every step degrees): the 5 best by the distance left to (x, z), else by speed; commit=true '
             'keeps the best. No emulator: dozens of tries take a fraction of a second.',
     {'name': 'string', 'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'spread': 'number', 'step': 'number',
      'frames': 'number', 'buttons': 'array', 'k': 'number', 'params': 'object', 'commit': 'boolean'}, []),
    ('goto', 'To (x, z): style auto (default: chained long jumps when far, running when near, swimming once in the '
             'water), walk, longjump or swim; y: a depth for the swim; enemies avoided; reached=true when it arrived '
             'within stop units; legs says what it did.',
     {'x': 'number', 'z': 'number', 'y': 'number', 'style': 'string', 'stop': 'number', 'frames': 'number'},
     ['x', 'z']),
    ('swim', 'In the water: swim to (x, z) (y: a depth; else level) with the fastest strokes (the swim strength built '
             'up stroke after stroke to the cap of 28 u/f), steered every frame, over underwater walls; out by a '
             'water jump when the target is above the water, exit=true, or the shore blocks Mario at the surface.',
     {'x': 'number', 'z': 'number', 'y': 'number', 'stop': 'number', 'frames': 'number', 'exit': 'boolean'},
     ['x', 'z']),
    ('collect', "Go and take an object (part of its behaviour name: 'coin', 'RedCoin', 'Star'...; near_x / near_z: "
                "the one nearest that point); collected=true only if a counter went up or the object is gone.",
     {'name': 'string', 'near_x': 'number', 'near_z': 'number'}, ['name']),
    ('close_dialog', 'Press A (one frame out of two) until the open dialog / text box closes; state() says '
                     "'blocked' when one holds the game.", {'frames': 'number'}, []),
    ('checkpoint', 'Remember the run so far under a name.', {'name': 'string'}, []),
    ('restore', 'Go back to a checkpoint.', {'name': 'string'}, []),
    ('undo', 'Take back the last n calls that played frames.', {'n': 'number'}, []),
    ('snapshot', "The game's whole state to a file (a headless snapshot, loaded back instantly).", {'path': 'string'},
     ['path']),
    ('export', 'Write the run as a .m64 (from power-on, or with its .st beside it) to replay in Mupen.',
     {'path': 'string'}, ['path']),
]


def tool_list():
    return [{'name': n, 'description': d,
             'inputSchema': {'type': 'object', 'required': req,
                             'properties': {k: ({'type': t, 'items': {}} if t == 'array' else
                                                {'type': t, 'additionalProperties': True} if t == 'object' else
                                                {'type': t})
                                            for k, t in props.items()}}}
            for n, d, props, req in TOOLS_SPEC]


def serve():
    play = Play()
    out = sys.stdout
    sys.stdout = sys.stderr                             # (the engine's prints must never reach the protocol)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, meth, prm = msg.get('id'), msg.get('method'), msg.get('params') or {}
        if mid is None:
            continue
        if meth == 'initialize':
            res = {'protocolVersion': prm.get('protocolVersion', '2024-11-05'), 'capabilities': {'tools': {}},
                   'serverInfo': {'name': 'sm64-headless', 'version': '1'}}
        elif meth == 'tools/list':
            res = {'tools': tool_list()}
        elif meth == 'tools/call':
            name, args = prm.get('name'), prm.get('arguments') or {}
            try:
                r = getattr(play, 't_' + name)(args)
                res = {'content': [{'type': 'text', 'text': json.dumps(r, default=str)}]}
            except Exception as e:
                res = {'content': [{'type': 'text', 'text': 'error: %s' % e}], 'isError': True}
                traceback.print_exc()
        elif meth == 'ping':
            res = {}
        else:
            out.write(json.dumps({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'no ' + str(meth)}})
                      + '\n')
            out.flush()
            continue
        out.write(json.dumps({'jsonrpc': '2.0', 'id': mid, 'result': res}) + '\n')
        out.flush()


def demo(st=None):
    p = Play()
    print('start', p.t_start({'st': st} if st else {}))
    sc = p.t_scan({'radius': 3000})
    print('scan: %d objects, %d floor levels, %d walls; nearest %s' % (
        len(sc['objects']), len(sc.get('floors', [])), len(sc.get('walls', [])),
        [(o['name'], o['dist']) for o in sc['objects'][:4]]))
    p.t_checkpoint({'name': 'a'})
    print('walk 40 frames:', {k: v for k, v in p.t_walk({'yaw_deg': 0, 'frames': 40}).items()
                              if k in ('forward_vel', 'action_name')})
    for mv in ('dive_rollout', 'triple_jump', 'lj_chain'):
        p.t_checkpoint({'name': 'm'})
        r = p.t_move({'name': mv})
        print('move %s: %d frames, %s, speed %s%s' % (mv, r['frames_played'], r['action_name'], r['forward_vel'],
                                                     ' (%s)' % r['warning'] if r.get('warning') else ''))
        p.t_restore({'name': 'm'})
    t0 = time.time()
    b = p.t_best({'frames': 20, 'yaw_deg': 0, 'spread': 60, 'step': 5})
    print('best: %d walks in %.2f s, the top 3 %s' % (b['tried'], time.time() - t0, [(q['yaw_deg'], q['forward_vel'], q['action']) for q in b['best'][:3]]))
    p.t_restore({'name': 'a'})
    print('export:', p.t_export({'path': os.path.join(ROOT, 'out', 'demo.m64')}))


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--demo':
        demo(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        serve()
