"""Where the doors, pipes and paintings of the current area lead, read from the game's own warp tables, and the
names of the levels (decomp include/level_table.h)."""
import ctypes
import math

import n64mem as NM

LEVELS = {4: 'Big Boo\'s Haunt', 5: 'Cool, Cool Mountain', 6: 'Castle inside', 7: 'Hazy Maze Cave',
          8: 'Shifting Sand Land', 9: 'Bob-omb Battlefield', 10: 'Snowman\'s Land', 11: 'Wet-Dry World',
          12: 'Jolly Roger Bay', 13: 'Tiny-Huge Island', 14: 'Tick Tock Clock', 15: 'Rainbow Ride',
          16: 'Castle grounds', 17: 'Bowser in the Dark World', 18: 'Vanish Cap under the Moat',
          19: 'Bowser in the Fire Sea', 20: 'The Secret Aquarium', 21: 'Bowser in the Sky', 22: 'Lethal Lava Land',
          23: 'Dire, Dire Docks', 24: 'Whomp\'s Fortress', 25: 'Ending', 26: 'Castle courtyard',
          27: 'Peach\'s Secret Slide', 28: 'Cavern of the Metal Cap', 29: 'Tower of the Wing Cap',
          30: 'Bowser 1 fight', 31: 'Wing Mario over the Rainbow', 33: 'Bowser 2 fight', 34: 'Bowser 3 fight',
          36: 'Tall, Tall Mountain'}
CASTLE_AREAS = {1: 'ground floor', 2: 'upper floors', 3: 'basement'}
PAINTING_D3 = 0xD3
# where Mario arrives back (after a death, a star, a launch): not a way out
ARRIVALS = ('Death', 'StarCollect', 'Airborne', 'KnockBack', 'Launch', 'Spawn', 'InstantActive')
PAINTING_N = 45


def level_name(level, area=None):
    n = LEVELS.get(int(level), 'level %d' % int(level))
    if int(level) == 6 and area in CASTLE_AREAS:
        n += ' (%s)' % CASTLE_AREAS[area]
    return n


def _members(name):
    return {m[3]: m[1] for m in NM.members(NM.find_type(name))}


def _node(g, a):
    i, lv, ar, nd = ctypes.string_at(a, 4)
    lv &= 0x7F                                       # (0x80: WARP_NO_CHECKPOINT)
    return {'id': i, 'to_level': lv, 'to_area': ar, 'to_node': nd, 'to': level_name(lv, ar)}


def warps(g, mario=None):
    """-> list of {kind, x, y, z, dist, to, to_level, to_area, ...}: the object warps (doors, pipes, holes) linked to
    their object, then the paintings (their warp floors, decomp level_update.c get_painting_warp_node)"""
    area = g.read('gCurrentArea', 'Q')
    if not area:
        return []
    A, W = _members('Area'), _members('ObjectWarpNode')
    m = mario or g.mario()
    px, py, pz = m['pos_x'], m['pos_y'], m['pos_z']
    objs = {o['addr']: o for o in g.objects()}
    out = []
    p, n = g.read(area + A['warpNodes'], 'Q'), 0
    while p and n < 128:
        w = _node(g, p + W['node'])
        o = objs.get(g.read(p + W['object'], 'Q'))
        if o is not None and w['to_level'] in LEVELS and not any(k in o['bhv'] for k in ARRIVALS):
            out.append(dict(w, kind=o['bhv'], x=round(o['pos_x']), y=round(o['pos_y']), z=round(o['pos_z']),
                            dist=round(math.hypot(o['pos_x'] - px, o['pos_z'] - pz)), slot=o['slot']))
        p, n = g.read(p + W['next'], 'Q'), n + 1
    pw = g.read(area + A['paintingWarpNodes'], 'Q')
    if pw:
        spots = {}
        for s in g.surfaces('floor'):
            k = s['type'] - PAINTING_D3
            if 0 <= k < PAINTING_N:
                spots.setdefault(k, []).extend((s['v1'], s['v2'], s['v3']))
        for k, pts in sorted(spots.items()):
            w = _node(g, pw + 4 * k)
            if w['to_level'] not in LEVELS:
                continue
            x = sum(q[0] for q in pts) / len(pts)
            y = max(q[1] for q in pts)
            z = sum(q[2] for q in pts) / len(pts)
            same = [r for r in out if r['kind'] == 'painting' and r['to_level'] == w['to_level']
                    and math.hypot(r['x'] - x, r['z'] - z) < 700]
            if same:                         # (a painting is several warp floors side by side: one entry, its middle)
                r = same[0]
                r['_pts'].append((x, y, z))
                r['x'] = round(sum(q[0] for q in r['_pts']) / len(r['_pts']))
                r['z'] = round(sum(q[2] for q in r['_pts']) / len(r['_pts']))
                r['dist'] = round(math.hypot(r['x'] - px, r['z'] - pz))
                continue
            out.append(dict(w, kind='painting', x=round(x), y=round(y), z=round(z),
                            dist=round(math.hypot(x - px, z - pz)), _pts=[(x, y, z)]))
    for r in out:
        r.pop('_pts', None)
    out.sort(key=lambda r: r['dist'])
    return out
