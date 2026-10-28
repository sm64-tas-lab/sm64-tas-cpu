"""The fastest line to a goal, the way a TAS finds it: a beam search over the engine's moves.

Each node is a game state; it grows by every move that fits Mario's state (long jump chains, dive rollouts, triple
jumps, slide kicks, plain running for a few frames), each aimed at a few facings around the bearing to the goal. A node
is ranked by the frames it took plus the distance left at a good TAS speed, so a slow start that ends fast still wins
over a quick start that stalls. The search keeps the best few nodes per step, one per small cell of (position, speed,
action), and stops at the goal: the line is cut at the very frame the goal is met. Headless, thousands of moves are
played in seconds.
"""
import math
import time

GOAL_SPEED = 48.0                 # u/f: the best TAS ground speed (a long jump), so the ranking never overestimates
FACINGS = (0, -3, 3, -8, 8, -16, 16)   # degrees around the bearing to the goal
RUN_STEPS = (2, 4, 8, 12, 16)  # frames of plain running (the stick toward the goal)
MOVES = (('lj_chain', {'cycles': 1}), ('lj_chain', {'cycles': 2}), ('dive_rollout', {'cycles': 1}),
         ('triple_jump', {}), ('slide_kick', {}), ('crouch_lj', {}), ('jump_dive', {}))


class Goal:
    """reached when Mario is within stop of (x, z) (and dy of y when given; ground=True: not in the air), or when the
    level / area changes (level=True: a door or a warp is the goal)"""

    def __init__(self, x=None, z=None, y=None, stop=80.0, dy=200.0, level=False, ground=False):
        self.x, self.z, self.y, self.stop, self.dy, self.level = x, z, y, float(stop), float(dy), level
        self.ground = ground            # (a point: Mario standing or running there, not flying over it mid-jump)
        self.where = None

    def start(self, m):
        self.where = (m['level'], m['area'])

    def dist(self, m):
        if self.x is None:
            return 0.0
        return math.hypot(self.x - m['pos_x'], self.z - m['pos_z'])

    def met(self, m):
        if self.level and (m['level'], m['area']) != self.where:
            return True
        if self.x is None:
            return False
        if self.ground and m['action'] & 0x800:
            return False
        return self.dist(m) <= self.stop and (self.y is None or abs(self.y - m['pos_y']) <= self.dy)


def _bearing(m, goal):
    return math.degrees(math.atan2(goal.x - m['pos_x'], goal.z - m['pos_z'])) % 360.0


def _replay_cut(g, state, inputs, goal):
    """the inputs played again from state, cut at the first frame the goal is met -> (inputs kept, met)"""
    g.load(state)
    for i, f in enumerate(inputs):
        g.step(f)
        if goal.met(g.mario()):
            return inputs[:i + 1], True
    return inputs, False


def race(g, run, goal, seconds=15.0, beam=16, maxf=900, blocked=None, wet=None):
    """-> dict(found, inputs, frames, moves, dist_left, tried, seconds). g is left at the start state."""
    import macros as M
    t0 = time.time()
    root = g.save()
    h0 = len(g.history)
    m0 = g.mario()
    goal.start(m0)
    yaw = lambda deg: int(round(deg * 65536.0 / 360.0)) & 0xFFFF
    layer = [(0.0, root, [], [])]
    best, tried = None, 0
    closest = (goal.dist(m0), [], [])
    while layer and time.time() - t0 < seconds:
        nxt = {}
        for _, st, ins, names in layer:
            g.load(st)
            m = g.mario()
            base = _bearing(m, goal) if goal.x is not None else (int(m['face_yaw']) & 0xFFFF) * 360.0 / 65536.0
            opts = [('run %d' % n, None, n) for n in RUN_STEPS]
            for name, params in MOVES:
                ok = name in M.SPECS and M.in_domain(name, m["action"], m["forward_vel"], g)[0]
                if ok:
                    opts.append((name + ('' if not params else ' x%d' % params.get('cycles', 1)), (name, params), 0))
            for label, mv, n in opts:
                for off in FACINGS:
                    if time.time() - t0 > seconds:
                        break
                    g.load(st)
                    aim = yaw(base + off)
                    if mv is None:
                        seg = [run.press(aim, 0, 32.0) for _ in range(n)]
                    else:
                        seg, status = run.run(mv[0], aim, **mv[1])
                        if status in ('fail', 'locked') and len(seg) <= 2:
                            continue
                    tried += 1
                    if not seg:
                        continue
                    if blocked and blocked(g):
                        continue
                    seg, met = _replay_cut(g, st, list(seg), goal)
                    m = g.mario()
                    if wet and wet(g) and not met:
                        continue
                    frames = len(ins) + len(seg)
                    lab = '%s @%+d' % (label, off) if off else label
                    if met:
                        if best is None or frames < best[0]:
                            best = (frames, ins + seg, names + [lab])
                        continue
                    if frames >= maxf or (best is not None and frames >= best[0]):
                        continue
                    d = goal.dist(m)
                    if d < closest[0]:
                        closest = (d, ins + seg, names + [lab])
                    score = frames + d / GOAL_SPEED
                    if best is not None and score >= best[0]:
                        continue
                    cell = (int(m['pos_x'] // 60), int(m['pos_z'] // 60), int(m['pos_y'] // 100),
                            int(m['forward_vel'] // 4), m['action'])
                    if cell not in nxt or score < nxt[cell][0]:
                        nxt[cell] = (score, g.save(), ins + seg, names + [lab])
        layer = sorted(nxt.values(), key=lambda t: t[0])[:beam]
    g.load(root)
    del g.history[h0:]
    if best:
        return {'found': True, 'inputs': best[1], 'frames': best[0], 'moves': best[2], 'tried': tried,
                'seconds': round(time.time() - t0, 1)}
    return {'found': False, 'inputs': closest[1], 'frames': len(closest[1]), 'moves': closest[2],
            'dist_left': round(closest[0], 1), 'tried': tried, 'seconds': round(time.time() - t0, 1)}
