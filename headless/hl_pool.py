import math, multiprocessing as mp, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
_W = {}


def _init(inputs, phantom=True):
    sys.path.insert(0, HERE)
    from hl_server import Emu
    e = Emu(log=lambda *a: None)
    e.g.run([tuple(x) for x in inputs])
    _W['e'] = e
    _W['s'] = e.g.save()
    _W['ph'] = phantom


def _cam_yaw(e):
    ar = e.bus.read(0x8033B170 + 0x90, 'u32')
    cm = e.bus.read(ar + 0x24, 'u32')
    return float(e.bus.read(cm + 0x2, 's16'))


def _batch(job):
    e = _W['e']
    e.g.load(_W['s'])
    e.phantom = _W['ph']
    jid, frames, allf = job
    n = len(frames)
    r = e.op_replaylist({'frames': frames, 'sampleall': True} if allf else {'frames': frames, 'samples': [n - 1, n]})
    fr = r['frames']
    o = {'id': jid}
    if allf:
        o['traj'] = [[round(x['s']['pos_x'], 1), round(x['s']['pos_y'], 1), round(x['s']['pos_z'], 1), x['s']['action'], x['s']['num_stars']] for x in fr]
        o['states'] = [fr[-2]['s'] if len(fr) > 1 else fr[-1]['s'], fr[-1]['s']]
    else:
        o['states'] = [x['s'] for x in fr]
    return o


def _cmds(job):
    e = _W['e']
    e.g.load(_W['s'])
    jid, cmds = job
    traj, applied, last = [], [], None
    phantom = _W['ph']
    for c in cmds:
        c = list(c) + [None] * (7 - len(c))
        cy = _cam_yaw(e)
        d = None
        raw = c[5] is not None and int(c[5]) == 1
        if raw:
            pass
        elif c[2] is not None:
            px = e.bus.read(0x8033B170 + 0x3C, 'f32')
            pz = e.bus.read(0x8033B170 + 0x44, 'f32')
            d = math.atan2(float(c[2]) - px, float(c[3]) - pz) / (2 * math.pi) * 65536
            if c[4] is not None and int(c[4]) == 1:
                d += 32768
        elif c[0] is not None:
            d = float(c[0])
        sx = sy = 0
        if raw:
            sx, sy = int(c[0]), int(c[6] or 0)
        if d is not None:
            t = (d - cy) / 65536 * 2 * math.pi
            o2 = []
            for p in (121 * math.sin(t), -121 * math.cos(t)):
                q = int(round(p))
                o2.append(0 if abs(q) < 2 else int(max(-127, min(127, q + 6 * (1 if q > 0 else -1)))))
            sx, sy = o2
        b = int(c[1] or 0)
        extra = 0
        if phantom:
            e.g.step((0, 0, 0)); phantom = False; extra = 1
        e.g.step((sx, sy, b))
        st = e.pack()
        row = [round(st['pos_x'], 1), round(st['pos_y'], 1), round(st['pos_z'], 1), st['action'], st['num_stars'],
               round(st['forward_vel'], 1), int(st['face_yaw'])]
        for _ in range(extra):
            applied.append([0, 0, 0]); traj.append(row)
        applied.append([sx, sy, b])
        traj.append([round(st['pos_x'], 1), round(st['pos_y'], 1), round(st['pos_z'], 1), st['action'], st['num_stars'],
                     round(st['forward_vel'], 1), int(st['face_yaw'])])
        last = st
    return {'id': jid, 'traj': traj, 'inputs': applied, 'states': [last, last]}


class Pool:
    def __init__(self, inputs, n=None, phantom=True):
        self.n = n or max(1, mp.cpu_count() - 2)
        self.p = mp.get_context('spawn').Pool(self.n, initializer=_init, initargs=(inputs, phantom))
        self.next = 0

    def batch(self, trials, allf=False):
        jobs = []
        for t in trials:
            jobs.append((self.next, [[int(v) for v in f[:3]] for f in t], allf))
            self.next += 1
        return self.p.map(_batch, jobs, chunksize=max(1, len(jobs) // (self.n * 4)))

    def cmds(self, trials):
        jobs = []
        for t in trials:
            jobs.append((self.next, t))
            self.next += 1
        return self.p.map(_cmds, jobs, chunksize=max(1, len(jobs) // (self.n * 4)))

    def close(self):
        self.p.terminate()
