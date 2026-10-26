"""Python helpers for the headless engine (sm64hl): objects, stick, closed-loop control.
Uses sm64hl.Game directly (~40,000 frames/s), without going through the PowerShell bridge."""
import math, os, re, sys, struct, ctypes
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HL = os.path.join(ROOT, 'headless')
if HL not in sys.path:
    sys.path.insert(0, HL)
from sm64hl import Game, fmt, write_m64, read_inputs  # noqa

M0 = os.path.join(ROOT, 'MOVIE_0STAR')
OBJ_SIZE = 1392          # struct Object en 64 bits
RAW = 216                # rawData
N_OBJ = 240

# useful actions
ACT_IDLE = 0x0C400201
ACT_WALK = 0x04000440
ACT_LJ = 0x03000888
ACT_LJ_LAND = 0x00000479   # (0x479 = LJ landing)
ACT_JUMP = 0x03000880
ACT_DOUBLE = 0x03000881
ACT_TRIPLE = 0x01000882
ACT_FREEFALL = 0x0100088C
ACT_CROUCH_SLIDE = 0x04808459
ACT_SLIDE_KICK_SLIDE = 0x0080045A
ACT_DIVE = 0x0188088A
ACT_DIVE_SLIDE = 0x00880456
ACT_JUMP_KICK = 0x018008AC
ACT_KICK = 0x00800445

A, B, Z, START = 0x80, 0x40, 0x20, 0x10
DOOR_ACTS = (0x1320, 0x1321, 0x1322, 0x132E, 0x132F, 0x1331)   # doors: let the animation play out
FAST = True   # control without camera correction (fast search)


def wrap(a):
    a = int(round(a)) & 0xFFFF
    return a - 65536 if a > 32767 else a


def angdiff(a, b):
    return abs(wrap(a - b))


def yaw_to(x, z, tx, tz):
    return wrap(math.atan2(tx - x, tz - z) / (2 * math.pi) * 65536)


# --- Directional stick algorithm ported from SM64LuaRedux ---
# Table of 7784 precomputed (X, Y) pairs sorted by angle (Engine.inputs_for_angle / Angles.ANGLE).
_ANGLES_TABLE = None  # [(angle, X, Y), ...]


def _load_angles():
    global _ANGLES_TABLE
    if _ANGLES_TABLE is not None:
        return _ANGLES_TABLE
    bin_path = os.path.join(os.path.dirname(__file__), 'angles.bin')
    if os.path.isfile(bin_path):
        with open(bin_path, 'rb') as f:
            raw = f.read()
        n = len(raw) // 4
        _ANGLES_TABLE = [struct.unpack_from('<Hbb', raw, i * 4) for i in range(n)]
        return _ANGLES_TABLE

    # Fallback: read Angles.lua if the binary is not present
    alt_lua = os.environ.get('SM64_ANGLES_LUA', '')    # (SM64LuaRedux's src/core/Angles.lua)
    if os.path.isfile(alt_lua):
        with open(alt_lua, 'r', encoding='utf-8') as f:
            text = f.read()
        matches = re.findall(r'\{\s*angle\s*=\s*(\d+)\s*,\s*X\s*=\s*(-?\d+)\s*,\s*Y\s*=\s*(-?\d+)\s*\}', text)
        _ANGLES_TABLE = [(int(a), int(x), int(y)) for a, x, y in matches]
        # Automatically save the binary cache
        try:
            data = b''.join(struct.pack('<Hbb', a, x, y) for a, x, y in _ANGLES_TABLE)
            with open(bin_path, 'wb') as f:
                f.write(data)
        except Exception:
            pass
        return _ANGLES_TABLE
    raise RuntimeError("Angles table (angles.bin or Angles.lua) not found for the stick computation!")


def scale_inputs_to_magnitude(start_x, start_y, goal_mag):
    """Exact mirror of Engine.scale_inputs_to_magnitude from SM64LuaRedux."""
    if goal_mag >= 64:
        return start_x, start_y
    if start_x == 0:
        y0 = goal_mag + 6
        x0 = 0
    elif start_y == 0:
        x0 = goal_mag + 6
        y0 = 0
    else:
        k = goal_mag
        x, y = abs(start_x), abs(start_y)
        x2, y2 = x * x, y * y
        crazy = math.sqrt((4 * (k ** 2 - 72) * y2) / (x2 + y2) + (y2 * (-12 * x - 12 * y) ** 2) / ((x2 + y2) ** 2))
        x0 = math.floor(abs(x * crazy / (2 * y) + (6 * x2) / (x2 + y2) + (6 * x * y) / (x2 + y2)))
        y0 = math.floor(abs(0.5 * crazy + (6 * y2) / (x2 + y2) + (6 * x * y) / (x2 + y2)))
    if start_x < 0:
        x0 = -x0
    if start_y < 0:
        y0 = -y0
    return int(x0), int(y0)


def stick(dir_yaw, cam, mag=1.0):
    """Return [x, y] for the stick to reach dir_yaw with camera cam.

    Ported from SM64LuaRedux (Engine.inputs_for_angle / Angles.ANGLE):
    binary search in the Angles.ANGLE table (7784 exact N64 entries).
    """
    table = _load_angles()
    n = len(table)
    cam = 0 if cam is None else int(round(cam))
    goal = int(round(dir_yaw))

    # Circular normalisation, identical to Engine.lua:
    goal = goal - 65536
    while cam > goal:
        goal += 65536

    # Binary search, identical to Engine.lua (lines 592-605)
    minang, maxang = 0, n - 1
    while minang <= maxang:
        midang = (minang + maxang) // 2
        if table[midang][0] + cam < goal:
            minang = midang + 1
        elif table[midang][0] + cam == goal:
            minang = midang
            maxang = midang - 1
        else:
            maxang = midang - 1

    if minang >= n:
        minang = 0
        d1 = abs(table[0][0] + cam - (goal - 65536))
        d2 = abs(table[n - 1][0] + cam - goal)
        if d1 > d2:
            minang = n - 1

    _, bx, by = table[minang]

    if mag < 1.0:
        bx, by = scale_inputs_to_magnitude(bx, by, mag * 64.0)

    return [bx, by]


class Objs:
    def __init__(self, g):
        self.g = g
        self.pool = g.addr('gObjectPool')

    def base(self, i):
        return self.pool + i * OBJ_SIZE

    def active(self, i):
        return self.g.read(self.base(i) + 180, 'h') & 1

    def f(self, i, idx):
        return self.g.read(self.base(i) + RAW + 4 * idx, 'f')

    def s(self, i, idx):
        return self.g.read(self.base(i) + RAW + 4 * idx, 'i')

    def pos(self, i):
        return tuple(self.f(i, k) for k in (6, 7, 8))

    def near(self, x, z, r, y=None, ry=1e9):
        out = []
        for i in range(N_OBJ):
            if not self.active(i):
                continue
            px, py, pz = self.pos(i)
            if abs(px - x) < r and abs(pz - z) < r and (y is None or abs(py - y) < ry):
                out.append((i, px, py, pz))
        return out


def load(name):
    """load a .hls milestone (short name in MOVIE_0STAR, or a path)."""
    g = Game()
    p = name if os.path.isabs(name) else os.path.join(M0, name)
    g.load_file(p)
    return g


def blocked(g):
    """dialogue/pause/cutscene in progress (the game ignores the stick)."""
    if g.read('gDialogID', 'H') != 0xFFFF or g.read('sCurrPlayMode', 'i') != 0:
        return True
    a = g.mario()['action']
    lo = a & 0x1FF
    return (a & 0x1C0) == 0x100 or lo in (0x105, 0x106, 0x120, 0x121, 0x122, 0x12F, 0x132)


def close_dialog(g, maxf=300):
    """One frame out of two until the open (or upcoming) dialogue closes; returns the inputs."""
    ins, seen = [], False
    for k in range(maxf):
        if g.read('gDialogID', 'H') != 0xFFFF:
            seen = True
        elif seen and g.read('sCurrPlayMode', 'i') == 0:
            break
        f = (0, 0, A if k % 2 == 0 else 0)
        g.step(f); ins.append(f)
    return ins


def go_lj(g, tx, tz, stop=150, maxf=400, lvl=None, crouch_zone=None, ymin=-3600, target=None, done=None, prev0=0, lj_min=0, hurt_ok=False):
    """chained long jumps toward (tx, tz): punch-cancel start, A on landing, stick held.
    Safeguards: dialogue, level change, max. Returns (inputs, reason)."""
    P = Pilot(g); ins = []; lvl0 = g.mario()['level'] if lvl is None else lvl
    hp0 = g.read(g.ms, 'h', 234)
    prev = prev0
    for k in range(maxf):
        m = g.mario()
        if m['level'] != lvl0:
            return ins, 'niveau'
        if m['action'] in DOOR_ACTS:
            g.step((0, 0, 0)); ins.append((0, 0, 0)); prev = 0; continue
        if blocked(g):
            return ins, 'bloque'
        if burning(m):
            return ins, 'brule'
        if g.read(g.ms, 'h', 234) < hp0 and not hurt_ok:
            return ins, 'touche'
        if m['pos_y'] < ymin:
            return ins, 'chute'
        if done is not None and done(g):
            return ins, 'arrive'
        if target is not None:
            tx, tz = target(g)
        d = math.hypot(tx - m['pos_x'], tz - m['pos_z'])
        if d < stop:
            return ins, 'arrive'
        want = yaw_to(m['pos_x'], m['pos_z'], tx, tz)
        act = m['action']; air = bool(act & 0x800); al = angdiff(m['face_yaw'], want) < 1500
        cz = crouch_zone and crouch_zone(m)
        if cz == 'walk':
            # walking zone (tight turn): no LJ, just run
            b = 0
        elif cz and not air:
            # low zone (flames): crouched slide, short hitbox; LJ forbidden
            b = Z
        elif act == ACT_LJ_LAND:
            b = A if (al and not prev & A and d > lj_min) else 0
        elif air:
            b = Z if prev & Z else 0
        elif act == ACT_CROUCH_SLIDE:
            b = (Z if prev & A else A | Z) if d > lj_min else 0
        elif act == ACT_IDLE and al:
            b = B
        else:
            b = Z if (al and m['forward_vel'] > 10.5 and d > 500 and not prev & Z) else 0
        if act == ACT_IDLE and al and b == B:
            g.step((0, 0, B)); ins.append((0, 0, B)); prev = B; continue
        (P.fast if FAST else P.step_dir)(want, b)
        ins.append(g.history[-1]); prev = b
    return ins, 'max'


def burning(m):
    return m['action'] in (0x010208B4, 0x010208B5, 0x00020449, 0x010208B7)


def go_objs(g, targets, maxf=300, ymin=-3600, lead=0, lj_min=0):
    """LJ from platform to platform: targets = object indices, then optionally a static (x, z, stop).
    Target = current object position + lead frames of movement. Next one once Mario lands on it."""
    o = Objs(g); ins = []
    for t in targets:
        if isinstance(t, tuple):
            i2, why = go_lj(g, t[0], t[1], stop=t[2], maxf=maxf - len(ins), ymin=ymin,
                            prev0=ins[-1][2] if ins else 0)
            ins += i2
            if why != 'arrive':
                return ins, why
            continue
        last = {}
        def tgt(g, t=t, last=last):
            x, _, z = o.pos(t)
            px, pz = last.get('p', (x, z)); last['p'] = (x, z)
            return x + (x - px) * lead, z + (z - pz) * lead
        on = lambda g, t=t: (not (g.mario()['action'] & 0x800)) and floor_obj(g) == t
        i2, why = go_lj(g, 0, 0, stop=0, maxf=maxf - len(ins), ymin=ymin, target=tgt, done=on,
                        prev0=ins[-1][2] if ins else 0, lj_min=lj_min)
        ins += i2
        if why != 'arrive':
            return ins, why
    return ins, 'arrive'


def go_route(g, wps, maxf=600, crouch_zone=None, ymin=-3600, lj_min=0, hurt_ok=False):
    """chains go_lj over waypoints (x, z, stop). Safeguards: burning, falling below ymin."""
    ins = []
    for (x, z, stop) in wps:
        i2, why = go_lj(g, x, z, stop=stop, maxf=maxf - len(ins), crouch_zone=crouch_zone, ymin=ymin, lj_min=lj_min, hurt_ok=hurt_ok)
        ins += i2
        if why != 'arrive':
            return ins, why
    return ins, 'arrive'


def floor_obj(g):
    """index of the object Mario stands on (None = static floor)."""
    fl = g.read(g.ms, 'Q', 112)
    if not fl:
        return None
    ob = g.read(fl, 'Q', 48)
    if not ob:
        return None
    return (ob - g.addr('gObjectPool')) // OBJ_SIZE


def mario_extra(g):
    return {'floor_h': g.read(g.ms, 'f', 124), 'health': g.read(g.ms, 'h', 234), 'keys': g.read(g.ms, 'b', 232),
            'floor_obj': floor_obj(g)}


def poke_mario(g, x=None, y=None, z=None, yaw=None, fvel=None, action=None):
    """SANDBOX ONLY (never in a movie): writes Mario's state."""
    ms = g.ms
    if x is not None: g.write(ms, 'f', x, 0x3C)
    if y is not None: g.write(ms, 'f', y, 0x40)
    if z is not None: g.write(ms, 'f', z, 0x44)
    if yaw is not None: g.write(ms, 'h', wrap(yaw), 0x2E)
    if fvel is not None:
        g.write(ms, 'f', fvel, 0x54)
        yy = g.read(ms, 'h', 0x2E) / 65536 * 2 * math.pi
        g.write(ms, 'f', fvel * math.sin(yy), 0x48); g.write(ms, 'f', fvel * math.cos(yy), 0x50)
    if action is not None: g.write(ms, 'I', action, 0x0C)


class Pilot:
    """Frame-by-frame control with camera correction: the input is chosen so that intended_yaw = target."""

    def __init__(self, g):
        self.g = g

    def m(self):
        return self.g.mario()

    def step_dir(self, yaw, buttons=0, mag=1.0, tries=3):
        """plays 1 frame with the stick toward world direction yaw (None = neutral). Corrects the camera."""
        g = self.g
        if yaw is None:
            g.step((0, 0, buttons))
            return g.mario()
        s0 = g.save()
        cam = g.cam_yaw()
        best = None
        for _ in range(tries):
            sx, sy = stick(yaw, cam, mag)
            g.step((sx, sy, buttons))
            mm = g.mario()
            err = wrap(yaw - mm['intended_yaw'])
            if best is None or abs(err) < abs(best[0]):
                best = (err, (sx, sy, buttons))
            if abs(err) < 24 or mm['intended_mag'] == 0:
                return mm
            g.load(s0)
            cam = wrap(cam - err)
        g.load(s0)
        g.step(best[1])
        return g.mario()

    def fast(self, yaw, buttons=0, mag=1.0):
        """1 frame, stick computed from the current camera, no correction (fast search)."""
        g = self.g
        if yaw is None:
            g.step((0, 0, buttons))
        else:
            sx, sy = stick(yaw, g.cam_yaw(), mag)
            g.step((sx, sy, buttons))

def go_walk(g, tx, tz, stop=50, maxf=200, mag=1.0):
    """Walk/run straight toward (tx, tz) without jumping. Returns (inputs, reason)."""
    P = Pilot(g)
    ins = []
    for k in range(maxf):
        m = g.mario()
        if blocked(g):
            return ins, 'bloque'
        d = math.hypot(tx - m['pos_x'], tz - m['pos_z'])
        if d < stop:
            return ins, 'arrive'
        want = yaw_to(m['pos_x'], m['pos_z'], tx, tz)
        P.fast(want, 0, mag=mag)
        ins.append(g.history[-1])
    return ins, 'max'


def ground_pound(g, maxf=60):
    """Performs a full ground pound and waits for the landing."""
    ins = []
    m = g.mario()
    if not (m['action'] & 0x800):
        f = (0, 0, A)
        g.step(f)
        ins.append(f)
        for _ in range(6):
            f = (0, 0, 0)
            g.step(f)
            ins.append(f)
    f = (0, 0, Z)
    g.step(f)
    ins.append(f)
    for _ in range(maxf):
        m = g.mario()
        act = m['action']
        if act == 0x0080023C or not (act & 0x800):
            break
        f = (0, 0, 0)
        g.step(f)
        ins.append(f)
    return ins


def flank_and_grab(g, target_slot_or_bhv, approach_dist=120, flank_rad=320, maxf=250):
    """Circles the target from behind and grabs it (avoids the Speed Kick Dive).
    Returns (inputs, success)."""
    P = Pilot(g)
    ins = []
    for f in range(maxf):
        m = g.mario()
        if isinstance(target_slot_or_bhv, int):
            objs = g.objects()
            obj = objs[target_slot_or_bhv] if target_slot_or_bhv < len(objs) else None
        elif isinstance(target_slot_or_bhv, str):
            obj = g.find_object(target_slot_or_bhv)
        else:
            obj = target_slot_or_bhv

        if not obj:
            return ins, False

        if m['action'] in (0x00000383, 0x08000208, 0x08000448) or obj.get('held_state') == 1:
            return ins, True

        ox, oz = obj['pos_x'], obj['pos_z']
        oyaw = obj['face_yaw']
        dx = m['pos_x'] - ox
        dz = m['pos_z'] - oz
        dist = math.hypot(dx, dz)
        m_angle = math.atan2(dx, dz) / (2 * math.pi) * 65536
        d_angle = wrap(m_angle - oyaw)

        if abs(d_angle) < 0x3800:
            flank_side = 0x2000 if d_angle > 0 else -0x2000
            orbit_angle = wrap(oyaw + 0x8000 + flank_side)
            rad_angle = orbit_angle / 65536 * 2 * math.pi
            tx = ox + flank_rad * math.sin(rad_angle)
            tz = oz + flank_rad * math.cos(rad_angle)
            want = yaw_to(m['pos_x'], m['pos_z'], tx, tz)
            P.fast(want, 0, mag=1.0)
            ins.append(g.history[-1])
        else:
            want = yaw_to(m['pos_x'], m['pos_z'], ox, oz)
            m_facing_diff = wrap(m['face_yaw'] - want)
            if dist < 140 and abs(m_facing_diff) < 0x2000:
                sx, sy = stick(want, g.cam_yaw(), mag=0.25)
                f_inp = (sx, sy, B)
                g.step(f_inp)
                ins.append(f_inp)
            else:
                mag = 0.35 if dist < 220 else 0.7
                sx, sy = stick(want, g.cam_yaw(), mag=mag)
                f_inp = (sx, sy, 0)
                g.step(f_inp)
                ins.append(f_inp)
    return ins, False


def throw_held(g, target_x=None, target_z=None, target_yaw=None, align_frames=12):
    """Aligns the trajectory and throws the held object with B."""
    ins = []
    m = g.mario()
    if target_yaw is None and target_x is not None and target_z is not None:
        target_yaw = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z)

    for _ in range(align_frames):
        if target_yaw is not None:
            sx, sy = stick(target_yaw, g.cam_yaw(), mag=1.0)
            f = (sx, sy, 0)
        else:
            f = (0, 0, 0)
        g.step(f)
        ins.append(f)

    f = (0, 0, B)
    g.step(f)
    ins.append(f)

    for _ in range(15):
        f = (0, 0, 0)
        g.step(f)
        ins.append(f)
    return ins



def wait_frames(g, count=1, btn=0):
    """Advances N frames without moving (optional button)."""
    ins = []
    for _ in range(count):
        f = (0, 0, btn)
        g.step(f)
        ins.append(f)
    return ins


def advance_dialog(g, max_frames=120, btn=0x80):
    """Mashes A (every other frame) to get past a dialogue or cutscene."""
    ins = []
    for f in range(max_frames):
        b = btn if f % 2 == 0 else 0
        inp = (0, 0, b)
        g.step(inp)
        ins.append(inp)
        if not blocked(g):
            break
    return ins


def long_jump_to(g, target_x, target_z, thresh=150.0, max_frames=120):
    """Performs a Long Jump aimed at the target coordinates (target_x, target_z)."""
    ins = []
    # 1. Run-up forward (3 frames)
    for _ in range(3):
        m = g.mario()
        want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z)
        sx, sy = stick(want, g.cam_yaw(), mag=1.0)
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)

    # 2. Trigger the Long Jump with Z + A
    m = g.mario()
    want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z)
    sx, sy = stick(want, g.cam_yaw(), mag=1.0)
    f = (sx, sy, Z | A)
    g.step(f)
    ins.append(f)

    # 3. Hold heading until landing or near the target
    for _ in range(max_frames):
        m = g.mario()
        d = math.hypot(target_x - m['pos_x'], target_z - m['pos_z'])
        if d < thresh or m['action'] in (ACT_IDLE, ACT_WALK, ACT_LJ_LAND):
            break
        want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z)
        sx, sy = stick(want, g.cam_yaw(), mag=1.0)
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)

    return ins


def slide_kick(g, target_x=None, target_z=None, duration=15, run_frames=8):
    """Performs a slide kick aimed at a target.
    Run-up -> Z (crouch slide) -> B (slide kick)."""
    ins = []
    # 1. Run-up
    for _ in range(run_frames):
        m = g.mario()
        cam = g.cam_yaw() or 0
        if target_x is not None and target_z is not None:
            want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z)
        else:
            want = m['face_yaw']
        sx, sy = stick(want, cam, mag=1.0)
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)

    # 2. Trigger the Crouch Slide (Z)
    m = g.mario()
    cam = g.cam_yaw() or 0
    if target_x is not None and target_z is not None:
        want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z)
    else:
        want = m['face_yaw']
    sx, sy = stick(want, cam, mag=1.0)
    f = (sx, sy, Z)
    g.step(f)
    ins.append(f)

    # 3. Sliding kick (B)
    f = (sx, sy, B)
    g.step(f)
    ins.append(f)

    # 4. Active slide
    for _ in range(duration):
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)
        m = g.mario()
        if m['action'] not in (ACT_SLIDE_KICK_SLIDE, ACT_CROUCH_SLIDE):
            break

    return ins


def dive(g, target_x=None, target_z=None, from_jump=True, duration=20, recover_jump=True):
    """Performs a forward dive aimed at a target.
    from_jump=True: short A hop, then a B dive in the air.
    recover_jump=True: on landing on the belly, press A for a quick recovery."""
    ins = []
    # 1. Initial run
    for _ in range(3):
        m = g.mario()
        cam = g.cam_yaw() or 0
        want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z) if (target_x and target_z) else m['face_yaw']
        sx, sy = stick(want, cam, mag=1.0)
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)

    # 2. Initial jump or push
    m = g.mario()
    cam = g.cam_yaw() or 0
    want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z) if (target_x and target_z) else m['face_yaw']
    sx, sy = stick(want, cam, mag=1.0)
    if from_jump:
        f = (sx, sy, A)
        g.step(f)
        ins.append(f)
        for _ in range(3):
            f = (sx, sy, 0)
            g.step(f)
            ins.append(f)

    # 3. Dive with B
    f = (sx, sy, B)
    g.step(f)
    ins.append(f)

    # 4. Flight / slide phase
    for _ in range(duration):
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)
        m = g.mario()
        if m['action'] in (ACT_DIVE_SLIDE, ACT_IDLE, ACT_WALK):
            if recover_jump and m['action'] == ACT_DIVE_SLIDE:
                # Recovery jump
                f_rec = (sx, sy, A)
                g.step(f_rec)
                ins.append(f_rec)
            break

    return ins


def kick(g, target_x=None, target_z=None, hits=3):
    """Chains melee hits (Punch -> Punch -> circular Kick with hits=3)."""
    ins = []
    cam = g.cam_yaw() or 0
    m = g.mario()
    want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z) if (target_x and target_z) else m['face_yaw']
    sx, sy = stick(want, cam, mag=0.5)

    for h in range(hits):
        # B hit
        f = (sx, sy, B)
        g.step(f)
        ins.append(f)
        # Optimal combo delay (7 frames)
        for _ in range(7):
            f = (0, 0, 0)
            g.step(f)
            ins.append(f)

    return ins


# Enemy and hazard behaviors to avoid while navigating
ENEMY_BEHAVIORS = {
    'bhvGoomba', 'bhvBobomb', 'bhvPitBowlingBall', 'bhvBowlingBall',
    'bhvWaterBomb', 'bhvChainChomp', 'bhvSpindrift', 'bhvFlyguy',
    'bhvScuttlebug', 'bhvChuckya', 'bhvPiranhaPlant', 'bhvSnufit',
    'bhvMontyMole', 'bhvFirePiranhaPlant', 'bhvHeaveHo', 'bhvMrBlizzard',
    'bhvKoopa', 'bhvBulletBill', 'bhvFlyGuy', 'bhvEnemyLakitu'
}


def nav_stick(m, tx, tz, g=None, mag=1.0, avoid_dist=240.0):
    """Computes (sx, sy) directly, aimed at (tx, tz), from Mario's state m and his camera.
    If the Game instance `g` is given, it automatically detects and goes around enemies
    and hazards on the path (2D repulsive potential field avoidance).
    """
    mx, my, mz = m['pos_x'], m['pos_y'], m['pos_z']
    cam = m.get('cam_yaw', 0)
    if cam is None:
        cam = 0

    target_dx = tx - mx
    target_dz = tz - mz
    target_dist = math.hypot(target_dx, target_dz)
    if target_dist < 1e-4:
        return stick(0, cam, mag=0)

    # Unit vector toward the target
    fx = target_dx / target_dist
    fz = target_dz / target_dist

    # Enemy avoidance if the environment `g` is provided
    if g is not None:
        for o in g.objects():
            if not o.get('active'):
                continue
            bhv = o.get('bhv', '')
            if bhv in ENEMY_BEHAVIORS:
                ox, oy, oz = o.get('pos_x', 0), o.get('pos_y', 0), o.get('pos_z', 0)
                # Skip if the enemy is too high or too low relative to Mario
                if abs(oy - my) > 130:
                    continue
                edx = ox - mx
                edz = oz - mz
                dist = math.hypot(edx, edz)
                if 0 < dist < avoid_dist:
                    # Repulsive force, inversely proportional to the distance
                    repel_strength = (avoid_dist - dist) / avoid_dist * 1.8
                    rx = -edx / dist
                    rz = -edz / dist
                    # 2D cross product to orient the lateral detour
                    cross = fx * rz - fz * rx
                    lat_dir = 1.0 if cross >= 0 else -1.0
                    perpx = -fz * lat_dir
                    perpz = fx * lat_dir

                    fx += rx * repel_strength + perpx * repel_strength * 0.9
                    fz += rz * repel_strength + perpz * repel_strength * 0.9

    want = wrap(math.atan2(fx, fz) / (2 * math.pi) * 65536)
    return stick(want, cam, mag=mag)


def enter_and_shoot_cannon(g, sx_steps, sy_steps, wait_prep=121):
    """
    Waits until Mario is ready in the cannon (c_act = 1),
    adjusts the horizontal aim (sx_steps frames) and vertical aim (sy_steps frames),
    and fires with A. Returns the list of inputs.
    """
    ins = []
    # Wait for the cannon to get ready
    for _ in range(wait_prep):
        f = (0, 0, 0)
        g.step(f)
        ins.append(f)
        
    # Horizontal aim (pan)
    if sx_steps > 0:
        for _ in range(sx_steps):
            f = (127, 0, 0)
            g.step(f)
            ins.append(f)
    elif sx_steps < 0:
        for _ in range(-sx_steps):
            f = (-127, 0, 0)
            g.step(f)
            ins.append(f)
            
    # Vertical aim (pitch)
    if sy_steps > 0:
        for _ in range(sy_steps):
            f = (0, 127, 0)
            g.step(f)
            ins.append(f)
    elif sy_steps < 0:
        for _ in range(-sy_steps):
            f = (0, -127, 0)
            g.step(f)
            ins.append(f)
            
    # Fire with A
    f = (0, 0, A)
    g.step(f)
    ins.append(f)
    return ins


def jump_dive(g, frames_jump=3, target_x=None, target_z=None, duration=20, recover_jump=True):
    """
    Performs a short jump followed by an air dive.
    Ideal for hitting floating blocks or quickly getting past obstacles.
    """
    ins = []
    cam = g.cam_yaw() or 0
    m = g.mario()
    want = yaw_to(m['pos_x'], m['pos_z'], target_x, target_z) if (target_x and target_z) else m['face_yaw']
    sx, sy = stick(want, cam)

    # 1. Jump with A
    for _ in range(frames_jump):
        f = (sx, sy, A)
        g.step(f)
        ins.append(f)

    # 2. Dive with B in the air
    f = (sx, sy, B)
    g.step(f)
    ins.append(f)

    # 3. Hold the slide
    for _ in range(duration):
        f = (sx, sy, 0)
        g.step(f)
        ins.append(f)

    # 4. Immediate recovery with A
    if recover_jump:
        f = (sx, sy, A)
        g.step(f)
        ins.append(f)

    return ins
