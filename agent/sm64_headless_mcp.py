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
        out['coins'], out['stars'] = g.coins, g.stars
        out['health'] = (g.health >> 8) & 0xFF
        out['frames'] = self._frames()
        return out

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
        """a game: from a Mupen savestate (st), a movie / input file (movie: .m64 / .hls / .json, from power-on), or
        power-on itself; version us / jp"""
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
        a0 = self.M.NAME.get(g.mario()['action'])
        inp = self.M.run_named(self.run, name, self.aim(a), k=int(a.get('k', 1)))
        self._push('move ' + name)
        out = self.state()
        out['frames_played'] = len(inp)
        if len(inp) <= 2:          # (B while walking under speed 29 punches instead of diving: said, never hidden)
            out['warning'] = ('the move stopped after %d frame(s): from %s the game did not take it (now %s); '
                              'check its entry (speed, action) or undo' % (len(inp), a0, out['action_name']))
        return out

    def t_moves(self, a):
        self.need()
        return {'moves': sorted(self.M.SPECS)}

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

    def t_goto(self, a):
        """to (x, z): running (style walk) or chained long jumps (style longjump), enemies avoided"""
        g = self.need()
        import hlx
        tx, tz = float(a['x']), float(a['z'])
        if str(a.get('style', 'walk')) == 'longjump':
            ins, why = hlx.go_lj(g, tx, tz, stop=float(a.get('stop', 80)), maxf=int(a.get('frames', 400)))
        else:
            ins, why = hlx.go_walk(g, tx, tz, stop=float(a.get('stop', 50)), maxf=int(a.get('frames', 300)))
        self._push('goto')
        m = g.mario()
        return {'reached': why in ('arrive', 'arrivé', 'arrived'), 'why': why, 'frames': len(ins),
                'dist_left': round(math.hypot(tx - m['pos_x'], tz - m['pos_z']), 1), 'mario': self.state()}

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
        far = math.hypot(o['pos_x'] - m['pos_x'], o['pos_z'] - m['pos_z']) > 800
        r = self.t_goto({'x': o['pos_x'], 'z': o['pos_z'], 'stop': 30, 'style': 'longjump' if far else 'walk'})
        got = taken()
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
                'mario': self.state()}

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
    ('start', 'Open a game: st = a Mupen savestate, or movie = a .m64 / .hls played from power-on, or nothing '
              '(power-on); version us / jp. Call once.', {'st': 'string', 'movie': 'string', 'version': 'string'}, []),
    ('state', "Mario: position, speeds, facing, action, level / area, coins, stars, health, frames played.", {}, []),
    ('scan', "Read from RAM: objects (behaviour name, position, distance, height above Mario), floors by height, "
             "walls. filter='coin' / 'star' / any part of a behaviour name finds every match in the level.",
     {'radius': 'number', 'filter': 'string', 'limit': 'number', 'geometry': 'boolean'}, []),
    ('step', 'Raw controller frames: stick_x / stick_y (-128..127), buttons (A B Z START R L CU CD CL CR), frames; or '
             'inputs = [[x, y, hi, lo], ...].',
     {'frames': 'number', 'stick_x': 'number', 'stick_y': 'number', 'buttons': 'array', 'inputs': 'array'}, []),
    ('walk', 'The stick toward (x, z) or a world direction yaw_deg (0 = +z, 90 = +x) for frames, the camera handled '
             'each frame; buttons held; mag 0..32.',
     {'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'frames': 'number', 'buttons': 'array', 'mag': 'number'}, []),
    ('move', "One of the engine's moves (see moves: dive_rollout, lj_chain, slide_kick, triple_jump, wall_kick, blj...) "
             "aimed at (x, z) or yaw_deg; k: its cycles / hold. A move the game refuses comes back with a warning.",
     {'name': 'string', 'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'k': 'number'}, ['name']),
    ('moves', "The engine's moves.", {}, []),
    ('try', 'A walk or a move (same arguments) played and taken back: what it would give.',
     {'name': 'string', 'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'frames': 'number', 'buttons': 'array',
      'mag': 'number', 'k': 'number'}, []),
    ('best', 'A move (name) or a walk (frames) tried at many facings at once (spread +- degrees around (x, z) or '
             'yaw_deg, every step degrees): the 5 best by the distance left to (x, z), else by speed; commit=true '
             'keeps the best. No emulator: dozens of tries take a fraction of a second.',
     {'name': 'string', 'x': 'number', 'z': 'number', 'yaw_deg': 'number', 'spread': 'number', 'step': 'number',
      'frames': 'number', 'buttons': 'array', 'k': 'number', 'commit': 'boolean'}, []),
    ('goto', 'To (x, z): style walk (running) or longjump (chained long jumps); enemies avoided; reached=true when '
             'it arrived within stop units.',
     {'x': 'number', 'z': 'number', 'style': 'string', 'stop': 'number', 'frames': 'number'}, ['x', 'z']),
    ('collect', "Go and take an object (part of its behaviour name: 'coin', 'RedCoin', 'Star'...; near_x / near_z: "
                "the one nearest that point); collected=true only if a counter went up or the object is gone.",
     {'name': 'string', 'near_x': 'number', 'near_z': 'number'}, ['name']),
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
                             'properties': {k: ({'type': t, 'items': {}} if t == 'array' else {'type': t})
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
