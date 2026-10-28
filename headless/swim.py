"""Swimming, the way a TAS swims: the fastest strokes, steered every frame, and a water jump out.

The strokes (decomp mario_actions_submerged.c, measured in the castle grounds' lake):
- a stroke (BREASTSTROKE) lasts 14 frames, then the game goes to FLUTTER_KICK on its own;
- A released on that FLUTTER_KICK's first frame: the swim strength +10 and SWIMMING_END;
- A down on SWIMMING_END's 7th frame: +10 again and a new stroke that keeps the strength;
- the strength (160 .. 280) is the speed above which the water slows Mario (strength / 10), so these chained strokes
  climb to the cap of 28 u/f, where a new A press inside a stroke (frames 2-5) would start it over at strength 160.
Steering: the stick's x turns Mario (his yaw speed, not a world direction: the camera plays no part), its y is his
pitch (stick up = nose down). Out of the water: A with the stick pulled back hard (y < -60) and Mario within 80 units
of the surface, nose level or up -> WATER_JUMP (62 u/f up).
"""
import math

WATER_ACTS = ('WATER_IDLE', 'WATER_ACTION_END', 'BREASTSTROKE', 'SWIMMING_END', 'FLUTTER_KICK', 'WATER_PLUNGE',
              'WATER_PUNCH', 'HOLD_WATER_IDLE', 'HOLD_WATER_ACTION_END', 'HOLD_BREASTSTROKE', 'HOLD_SWIMMING_END',
              'HOLD_FLUTTER_KICK', 'WATER_SHELL_SWIMMING', 'BACKWARD_WATER_KB', 'FORWARD_WATER_KB')
STROKE_FRAMES = 14
MAX_PITCH = 0x3000


def _raw(v):
    """the raw stick value the game reads back as v (-64..64): the dead zone is 8, the game takes 6 off"""
    v = int(round(max(-64.0, min(64.0, v))))
    return 0 if v == 0 else (v + 6 if v > 0 else v - 6)


class Swimmer:
    """reads Mario's swim state straight from RAM and gives each frame's input"""

    def __init__(self, g):
        import n64mem as NM
        import macros as M
        self.g, self.M = g, M
        mm = {m[3]: m[1] for m in NM.members(NM.find_type('MarioState'))}
        self.ms = g.addr('gMarioStates')
        self.o = {k: mm[k] for k in ('action', 'actionTimer', 'faceAngle', 'angleVel', 'pos', 'forwardVel',
                                      'waterLevel')}
        self.water = {M.ACT[n] for n in WATER_ACTS if n in M.ACT}

    def act(self):
        return self.g.read(self.ms, 'I', self.o['action'])

    def name(self):
        a = self.act()
        return self.M.NAME.get(a, hex(a))

    def in_water(self):
        return self.act() in self.water

    def read(self):
        g, ms, o = self.g, self.ms, self.o
        return {'timer': g.read(ms, 'H', o['actionTimer']),
                'pitch': g.read(ms, 'h', o['faceAngle']), 'yaw': g.read(ms, 'h', o['faceAngle'] + 2),
                'yaw_vel': g.read(ms, 'h', o['angleVel'] + 2),
                'pos': [g.read(ms, 'f', o['pos'] + 4 * k) for k in range(3)],
                'fv': g.read(ms, 'f', o['forwardVel']), 'water': g.read(ms, 'h', o['waterLevel'])}

    def stroke_a(self, name, timer, prev_a):
        """A for the fastest chained strokes (see the module's doc)"""
        if name in ('BREASTSTROKE', 'HOLD_BREASTSTROKE'):
            return timer < STROKE_FRAMES - 1
        if name in ('FLUTTER_KICK', 'HOLD_FLUTTER_KICK'):
            return False
        if name in ('SWIMMING_END', 'HOLD_SWIMMING_END'):
            return timer >= 6
        if name in ('WATER_PLUNGE',):
            return True                         # (A down through the plunge: it ends in a flutter kick, not idle)
        return not prev_a                       # (idle: a new press starts a stroke)

    def steer(self, s, tx, ty, tz):
        """the stick (x, y the game reads, -64..64) turning Mario toward (tx, ty, tz)"""
        p = s['pos']
        want = int(round(math.atan2(tx - p[0], tz - p[2]) * 32768.0 / math.pi))
        err = ((want - s['yaw'] + 0x8000) & 0xFFFF) - 0x8000
        # the yaw speed that brakes in time: v^2 / (2 * 0x10) per frame of deceleration, at most 640 (stick 64)
        v = math.copysign(min(640.0, math.sqrt(2.0 * 16.0 * abs(err))), err) if abs(err) > 8 else 0.0
        sx = -v / 10.0
        horiz = math.hypot(tx - p[0], tz - p[2])
        if ty is None:
            pitch = 0
        else:
            pitch = int(round(math.atan2(ty - p[1], max(horiz, 1.0)) * 32768.0 / math.pi))
            pitch = max(-MAX_PITCH, min(MAX_PITCH, pitch))
        sy = -pitch / 252.0
        m = math.hypot(sx, sy)
        if m > 64.0:
            sx, sy = sx * 64.0 / m, sy * 64.0 / m
        return sx, sy

    def frame(self, tx, ty, tz, prev_a, jump=False):
        """one frame's input (x, y, hi, lo) and whether A is in it"""
        s = self.read()
        name = self.name()
        if jump:
            sx, _ = self.steer(s, tx, None, tz)
            a = not prev_a
            return (_raw(sx * 0.3), -128, 0x80 if a else 0, 0), a
        sx, sy = self.steer(s, tx, ty, tz)
        a = self.stroke_a(name, s['timer'], prev_a)
        return (_raw(sx), _raw(sy), 0x80 if a else 0, 0), a


def swim_to(g, tx, tz, ty=None, stop=100.0, maxf=900, exit_out=None):
    """swim to (tx, ty, tz) (ty None: at the depth Mario is at, steered level). exit_out: True jumps out of the water
    (a WATER_JUMP) once at the surface within stop of the target, None does it when the target is above the water.
    -> (inputs, why): why 'arrived', 'jumped out', 'not in water', 'left the water', 'max'"""
    sw = Swimmer(g)
    if not sw.in_water():
        return [], 'not in water'
    out, prev_a, trail, climb, shore = [], False, [], 0, False
    for _ in range(maxf):
        if not sw.in_water():
            return out, 'jumped out' if sw.name() in ('WATER_JUMP', 'HOLD_WATER_JUMP') else 'left the water'
        s = sw.read()
        p = s['pos']
        d = math.hypot(tx - p[0], tz - p[2])
        up = shore or (exit_out if exit_out is not None else (ty is not None and ty > s['water']))
        aim_y = (s['water'] - 40.0) if up and d < stop + 600.0 else ty
        trail.append((p[0], p[2], s['fv']))
        if len(trail) > 6:
            trail.pop(0)
            moved = math.hypot(trail[-1][0] - trail[0][0], trail[-1][1] - trail[0][1])
            if moved < 0.3 * sum(t[2] for t in trail[1:]) and s['fv'] > 8.0:
                climb = 30                      # (swimming into a wall or a rising floor: over it, toward the surface)
                if p[1] + 1.5 >= s['water'] - 80:
                    shore, up, stop = True, True, max(stop, d)   # (at the surface against the shore: a water jump out)
        if climb:
            climb -= 1
            aim_y = s['water'] - 40.0
        at_top = p[1] + 1.5 >= s['water'] - 80 and s['pitch'] >= 0
        if up and d <= stop + 300.0 and at_top:
            f, prev_a = sw.frame(tx, None, tz, prev_a, jump=True)
        else:
            if not up and d <= stop and (ty is None or abs(ty - p[1]) <= stop):
                return out, 'arrived'
            f, prev_a = sw.frame(tx, aim_y, tz, prev_a)
        g.step(f)
        out.append(f)
    return out, 'max'
