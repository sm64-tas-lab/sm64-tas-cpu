import os
import numpy as np
from sm64hl import Game, read_m64
F14 = os.environ.get('BASE') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tylerkehne,1key', 'f14', 'f14_1key_jp_NOHACK_full.m64')
inp = [tuple(f) for f in read_m64(F14)]
g = Game(version='jp')
for f in inp[:2335]: g.step(f)
st = g.save()
cy = g.cam_yaw()
T = np.zeros((256, 256, 2), np.float32)
for x in range(-128, 128):
    for y in range(-128, 128):
        g.load(st); g.step((x, y, 0, 16)); m = g.mario()
        T[x + 128, y + 128] = ((m['intended_yaw'] - cy) & 0xFFFF, m['intended_mag'])
np.save('_stick_table.npy', T)
print('ok', cy, T[128 + 127, 128], T[128, 128 + 127])
