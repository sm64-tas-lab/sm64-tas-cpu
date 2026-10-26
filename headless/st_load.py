import ctypes
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import n64map_build as B
import n64mem as NM
import st_bridge as SB
from sm64hl import version_info

VERSION = os.environ.get('SM64_VERSION', 'us')
_SECS = version_info(VERSION)[1]
VARS_CACHE = '_vars_cache.json' if VERSION == 'us' else f'_vars_cache_{VERSION}.json'

BASE_HLS = os.path.join(HERE, '07.hls')
WARP_CHANGE_LEVEL, PLAY_MODE_NORMAL, PLAY_MODE_CHANGE_LEVEL = 1, 0, 4
SKIP = set(SB.NOISE) | {'gSaveBuffer'}
SKIP_PREFIX = ('gAudio', 'sAudio', 'gSeq', 'gNote', 'gBank', 'gCtl', 'gAl', 'gSound', 'sSound', 'gGfx', 'sGfx',
               'gDisplayList', 'gMainPool', 'sPool', 'gDma', 'gGameMesg', 'gProfiler', 'gGameVblank', 'gGfxVblank',
               'sRenderingFramebuffer', 'gPhysicalFrameBuffers', 'gPhysicalZBuffer', 'gFrameBuffer')


IMAGE_SIZE = 0x2400000
GAME_STATE_LO = 0x80246000


def n64_map(everything=False):
    ver = os.environ.get('SM64_VERSION', 'us')
    if ver != 'us':
        return {k: a for k, a in json.load(open(os.path.join(HERE, f'_n64map_{ver}.json')))['globals'].items()
                if k == 'gSaveBuffer' or a >= GAME_STATE_LO}
    m = dict(json.load(open(os.path.join(HERE, '_n64map.json')))['globals'])
    p = os.path.join(HERE, '_n64map_ext.json')
    if os.path.exists(p):
        for k, v in json.load(open(p))['globals'].items():
            m.setdefault(k, v)
    fix = os.path.join(HERE, '_n64map_fix.json')
    if os.path.exists(fix):
        f = json.load(open(fix))
        m.update({k: int(v, 0) if isinstance(v, str) else v for k, v in f.get('set', {}).items()})
        for k in f.get('remove', []):
            m.pop(k, None)
    keep = {'gSaveBuffer'}
    out = {}
    for k, a in m.items():
        v = var_info(k)
        cu = (v or {}).get('cu', '').replace(chr(92), '/')
        if k in keep or (a >= GAME_STATE_LO and cu.startswith(GAME_CUS) and (everything or not cu.endswith(NATIVE_CUS))):
            out[k] = a
    return out


NATIVE_CUS = ('src/engine/level_script.c', 'src/game/memory.c')
GAME_CUS = ('src/game/', 'src/engine/')


_VARS = None


def var_info(key):
    global _VARS
    if _VARS is None:
        _VARS = {}
        for v in json.load(open(os.path.join(HERE, VARS_CACHE)))['vars']:
            k = f"{v['fn']}:{v['n']}" if v['fn'] else v['n']
            _VARS.setdefault(k, v)
    return _VARS.get(key) or NM.G.get(key)


class St:

    def __init__(self, path_or_rd):
        self.path = path_or_rd if isinstance(path_or_rd, str) else None
        self.rd = SB.rdram(path_or_rd) if isinstance(path_or_rd, str) else path_or_rd
        self.rdn = np.frombuffer(self.rd, np.uint8)
        self.map = n64_map()
        self.all = n64_map(everything=True)

    def get(self, fmt, a):
        return struct.unpack_from('>' + fmt, self.rd, (a & 0xFFFFFFFF) - 0x80000000)[0]

    def var(self, name, fmt='h', off=0):
        return self.get(fmt, self.map[name] + off)

    def info(self):
        area = self.var('gCurrAreaIndex')
        return {'level': self.var('gCurrLevelNum'), 'area': area, 'act': self.var('gCurrActNum'),
                'course': self.var('gCurrCourseNum'), 'save_file': self.var('gCurrSaveFileNum'),
                'warp_node': self.entry_node(area), 'timer': self.var('gGlobalTimer', 'I')}

    def warp_nodes(self, area):
        t = NM.find_type('Area')
        a_n = NM.layout(t)[0]
        off = {m[3]: m[0] for m in NM.members(t)}['warpNodes']
        p = self.get('I', self.map['gAreaData'] + area * a_n + off)
        ids = []
        while 0x80000000 <= p < 0x80800000 and len(ids) < 256:
            ids.append(self.get('B', p))
            p = self.get('I', p + 8)
        return ids

    def entry_node(self, area):
        ids = self.warp_nodes(area)
        n = self.var('sWarpDest', 'B', 3)
        same_level = self.var('sWarpDest', 'B', 1) == self.var('gCurrLevelNum')
        if (same_level and n in ids) or not ids:
            return n
        normal = [i for i in ids if i < 0xF0]
        return 0x0A if 0x0A in normal else (normal or ids)[0]


def warp_native(g, level, area, node, act=1, save_file=None, save_buffer=None, max_frames=600):
    snap = getattr(g, '_castle_milestone', None)
    if snap is None:
        g.load_file(BASE_HLS)
        g._castle_milestone = g.save()
    else:
        g.load(snap)
    if save_buffer is not None:
        ctypes.memmove(g.addr('gSaveBuffer'), save_buffer, len(save_buffer))
    if save_file:
        g.write('gCurrSaveFileNum', 'h', save_file)
    g.write('gCurrActNum', 'h', act)
    g.write('sWarpCheckpointActive', 'B', 1)
    g.write('sWarpDest', 'B', WARP_CHANGE_LEVEL, 0)
    g.write('sWarpDest', 'B', level, 1)
    g.write('sWarpDest', 'B', area, 2)
    g.write('sWarpDest', 'B', node, 3)
    g.write('sWarpDest', 'I', 0, 4)
    g.write('sTransitionTimer', 'h', 0)
    g.write('sCurrPlayMode', 'h', PLAY_MODE_CHANGE_LEVEL)
    for i in range(max_frames):
        g.step((0, 0, 0))
        if (g.read('gCurrLevelNum', 'h') == level and g.read('gCurrAreaIndex', 'h') == area
                and g.read('sCurrPlayMode', 'h') == PLAY_MODE_NORMAL and g.read('gMarioObject', 'Q')
                and g.read('gCurrActNum', 'h') == act):
            return i + 1
    raise RuntimeError(f'warp to level {level} area {area} node 0x{node:X}: not loaded after {max_frames} frames')


def _host_loc(rva):
    for sec in ('.data', '.bss'):
        va, vs = _SECS[sec]
        if va <= rva < va + vs:
            return sec, rva - va
    return None, None


def graft_values(g, st, names=None, skip_objects=True):
    mem, hist = g.save()
    arrs = {'.data': np.frombuffer(mem[0], np.uint8).copy(), '.bss': np.frombuffer(mem[1], np.uint8).copy()}
    done = []
    for name, a in st.map.items():
        if names is not None and name not in names:
            continue
        if name in SKIP or name.startswith(SKIP_PREFIX) or (skip_objects and name == 'gObjectPool'):
            continue
        v = var_info(name)
        if not v or not v['type']:
            continue
        sec, hoff = _host_loc(v['rva'])
        if sec is None:
            continue
        size = NM.layout(v['type'])[0]
        if not size or size > 0x40000 or a - 0x80000000 + size > len(st.rd):
            continue
        host = arrs[sec]
        prims, _ptrs, _objptr = B.flatten(v['type'])
        bn = a - 0x80000000
        for s, (no, ho) in prims.items():
            host[hoff + ho[:, None] + np.arange(s)[None, :]] = st.rdn[bn + no[:, None] + np.arange(s)[None, :]][:, ::-1]
        done.append(name)
    g.load(([arrs['.data'].tobytes(), arrs['.bss'].tobytes()], hist))
    return done


import bisect
import n64bus as NB
import st_consts as SC

O_TYPE = NM.find_type('Object')
O_N64, O_HOST = NM.layout(O_TYPE)[0], NM.layout(O_TYPE)[2]
RAW_N, RAW_H, PTR_H = NB._obj_offsets()
N_RAW = 0x50


class Translator:

    def __init__(self, g, st, romhack=False):
        self.g, self.st, self.romhack = g, st, romhack
        self.regions = []
        for name, a in st.map.items():
            v = var_info(name)
            if not v or not v['type']:
                continue
            sz = NM.layout(v['type'])[0]
            if sz:
                self.regions.append((a, sz, g.base + v['rva'], v['type'], name))
        self.regions.sort()
        self.starts = [r[0] for r in self.regions]
        self.bhv = SC.BhvMap(g, st, romhack=romhack)
        self.consts = SC.ConstMap(g, st, st.all['sSegmentTable']) if 'sSegmentTable' in st.all else None
        self.pairs = {}
        self.arrays = []
        self.misses = {}
        self.host_copies = {}

    def ram(self, v):
        if 0x80000000 <= v < 0x80000000 + len(self.st.rd):
            return v
        if 0x01000000 <= v < 0x20000000 and self.consts and (v >> 24) in self.consts.seg_base:
            return self.consts.seg_base[v >> 24] + (v & 0xFFFFFF)
        return None

    def host_word(self, v):
        a = self.ram(v)
        if a is None:
            return None
        if a not in self.host_copies:
            data = struct.pack('<I', self.st.get('I', a)) if a % 4 == 0 else struct.pack('<h', self.st.get('h', a))
            self.host_copies[a] = (_host_buffer(self.g, data), 'spawn word')
        return self.host_copies[a][0]

    def host_collision(self, v):
        a = self.ram(v)
        if a is None:
            return None
        if a in self.host_copies:
            return self.host_copies[a][0]
        n = parse_collision(self.st, a)
        if n is None:
            return None
        data = struct.pack(f'<{n}h', *(self.st.get('h', a + 2 * i) for i in range(n)))
        self.host_copies[a] = (_host_buffer(self.g, data), 'collision')
        return self.host_copies[a][0]

    def add_pair(self, n64, host):
        if n64 and host:
            self.pairs[n64] = host

    def add_array(self, n64, count, t, host):
        if n64 and host and count:
            n, _, h = NM.layout(t)[:3]
            self.arrays.append((n64, count, n, host, h, t))

    def _in_region(self, v):
        i = bisect.bisect_right(self.starts, v) - 1
        while i >= 0:
            a, sz, h, t, name = self.regions[i]
            if a <= v < a + sz:
                res = NB._resolve(t, v - a)
                if res is None:
                    return None
                pt, pn, ph, obj = res
                return h + ph + (v - a - pn)
            if a + 0x40000 < v:
                break
            i -= 1
        return None

    def to_host(self, v, where=None, shift=True):
        if v == 0:
            return 0
        for a, cnt, ns, h, hs, t in self.arrays:
            if a <= v < a + cnt * ns:
                k, r = divmod(v - a, ns)
                if r == 0:
                    return h + k * hs
                res = NB._resolve(t, r)
                return None if res is None else h + k * hs + res[2] + (r - res[1])
        if v in self.pairs:
            return self.pairs[v]
        h = self._in_region(v)
        if h is not None:
            return h
        h = self.bhv.to_host(v)
        if h is not None:
            return h
        h = self.consts.to_host(v, shift) if self.consts else None
        if h is not None:
            return h
        if where:
            self.misses.setdefault(where, set()).add(v)
        return None


def _write_q(addr, value):
    ctypes.memmove(addr, struct.pack('<Q', value), 8)


def _ptr_prims(t):
    _prims, plist, _objptr = B.flatten(t)
    return [(no, ho, 'pi' if pt is None else 'p') for no, ho, pt in plist]


_CONST_RANGES = None


def _const_target(g, hp):
    global _CONST_RANGES
    if _CONST_RANGES is None:
        _CONST_RANGES = [(_SECS[s][0], _SECS[s][0] + _SECS[s][1]) for s in ('.text', '.rdata') if s in _SECS]
    r = hp - g.base
    return any(a <= r < b for a, b in _CONST_RANGES)


def graft_pointers(g, st, tr, a, host, t, where, keep_unknown=True):
    n_ok = n_bad = 0
    for no, ho, kind in _ptr_prims(t):
        native = struct.unpack('<Q', ctypes.string_at(host + ho, 8))[0]
        if native and _const_target(g, native):
            continue
        v = st.get('I', a + no)
        if kind == 'pi' and not (0x80000000 <= v < 0x80800000):
            if not (g.base <= native < g.base + IMAGE_SIZE):
                _write_q(host + ho, v)
            continue
        h = tr.to_host(v, f'{where}+{no:X}')
        if h is None:
            n_bad += 1
            if not keep_unknown:
                _write_q(host + ho, 0)
            continue
        _write_q(host + ho, h)
        n_ok += 1
    return n_ok, n_bad


def register_heap(g, st, tr):
    na, ha = st.var('gLoadedGraphNodes', 'I'), g.read('gLoadedGraphNodes', 'Q')
    if na and ha:
        for k in range(256):
            vn = st.get('I', na + 4 * k)
            vh = struct.unpack('<Q', ctypes.string_at(ha + 8 * k, 8))[0]
            if not (vh and 0x80000000 <= vn < 0x80800000):
                continue
            if st.get('I', vn) == (g.read(vh, 'H', 0) << 16 | g.read(vh, 'H', 2)):
                tr.add_pair(vn, vh)
    tr.add_array(st.var('sSurfacePool', 'I'), st.var('gSurfacesAllocated', 'i'),
                 NM.find_type('Surface'), g.read('sSurfacePool', 'Q'))
    tr.add_array(st.var('sSurfaceNodePool', 'I'), st.var('gSurfaceNodesAllocated', 'i'),
                 NM.find_type('SurfaceNode'), g.read('sSurfaceNodePool', 'Q'))
    tr.add_pair(st.var('gCamera', 'I'), g.read('gCamera', 'Q'))
    t_area, t_si = NM.find_type('Area'), NM.find_type('SpawnInfo')
    a_n, _, a_h = NM.layout(t_area)[:3]
    off = {m[3]: (m[0], m[1]) for m in NM.members(t_area)}
    nxt = {m[3]: (m[0], m[1]) for m in NM.members(t_si)}['next']
    na, ha = st.map['gAreaData'], g.addr('gAreaData')
    for k in range(8):
        pn = st.get('I', na + k * a_n + off['objectSpawnInfos'][0])
        ph = struct.unpack('<Q', ctypes.string_at(ha + k * a_h + off['objectSpawnInfos'][1], 8))[0]
        while pn and ph:
            tr.add_array(pn, 1, t_si, ph)
            pn = st.get('I', pn + nxt[0])
            ph = struct.unpack('<Q', ctypes.string_at(ph + nxt[1], 8))[0]


def graft_heap(g, st, tr):
    out = {}
    t_s, t_n = NM.find_type('Surface'), NM.find_type('SurfaceNode')
    for name, t, cnt in (('sSurfacePool', t_s, st.var('gSurfacesAllocated', 'i')),
                         ('sSurfaceNodePool', t_n, st.var('gSurfaceNodesAllocated', 'i'))):
        na, ha = st.var(name, 'I'), g.read(name, 'Q')
        n64s, _, hs = NM.layout(t)[:3]
        cap = cnt if cnt <= g.read('gSurfacesAllocated' if t == t_s else 'gSurfaceNodesAllocated', 'i') else cnt
        for k in range(cap):
            copy_values(st, na + k * n64s, ha + k * hs, t)
            graft_pointers(g, st, tr, na + k * n64s, ha + k * hs, t, name)
        out[name] = cap
    ca, ch = st.var('gCamera', 'I'), g.read('gCamera', 'Q')
    if ca and ch:
        copy_values(st, ca, ch, NM.find_type('Camera'))
        graft_pointers(g, st, tr, ca, ch, NM.find_type('Camera'), 'Camera')
        out['camera'] = hex(ca)
    return out


_KEEP = []
SURFACE_HAS_FORCE = {0x0004, 0x000E, 0x0024, 0x0025, 0x0027, 0x002C, 0x002D}
TERRAIN_LOAD_VERTICES, TERRAIN_LOAD_CONTINUE, TERRAIN_LOAD_END = 0x40, 0x41, 0x42
SURFACE_NODE_POOL = 7000


def parse_collision(st, a, limit=0x8000):
    try:
        if st.get('h', a) != TERRAIN_LOAD_VERTICES:
            return None
        nv = st.get('h', a + 2)
        if not 0 < nv < 0x4000:
            return None
        i = 2 + 3 * nv
        while i < limit:
            t = st.get('h', a + 2 * i)
            if t == TERRAIN_LOAD_CONTINUE:
                break
            n = st.get('h', a + 2 * i + 2)
            if n < 0:
                return None
            i += 2 + n * (4 if t in SURFACE_HAS_FORCE else 3)
        else:
            return None
        j = i
        while j < i + 0x400 and st.get('h', a + 2 * j) != TERRAIN_LOAD_END:
            j += 1
        return (j if j < i + 0x400 else i) + 1
    except struct.error:
        return None


DYNAMIC_ROOM = 1000


class ImageArena:

    def __init__(self, g, n_surfaces):
        hs = NM.layout(NM.find_type('Surface'))[2]
        base = g.read('sSurfacePool', 'Q')
        self.top = base + g.read('sSurfacePoolSize', 'h') * hs
        self.floor = base + (n_surfaces + DYNAMIC_ROOM) * hs
        self.used = 0

    def alloc(self, n):
        a = (self.top - n) & ~7
        if a < self.floor:
            return None
        self.used += self.top - a
        self.top = a
        return a


def _host_buffer(g, data):
    ar = getattr(g, '_rh_arena', None)
    a = ar.alloc(len(data)) if ar else None
    if a is None:
        buf = ctypes.create_string_buffer(data, len(data))
        _KEEP.append(buf)
        g._rh_outside = getattr(g, '_rh_outside', 0) + 1
        return ctypes.addressof(buf)
    ctypes.memmove(a, data, len(data))
    return a


def ensure_pools(g, st):
    t_s, t_n = NM.find_type('Surface'), NM.find_type('SurfaceNode')
    out = {}
    g._rh_outside = 0
    for name, t, need, cap in (('sSurfacePool', t_s, st.var('gSurfacesAllocated', 'i'), g.read('sSurfacePoolSize', 'h')),
                               ('sSurfaceNodePool', t_n, st.var('gSurfaceNodesAllocated', 'i'), SURFACE_NODE_POOL)):
        want = need + DYNAMIC_ROOM
        if want <= cap:
            out[name] = cap
            continue
        want = need + 2000
        g._rh_outside += 1
        hs = NM.layout(t)[2]
        buf = (ctypes.c_uint8 * (want * hs))()
        _KEEP.append(buf)
        _write_q(g.addr(name), ctypes.addressof(buf))
        g.__dict__.setdefault('_rh_regions', []).append((ctypes.addressof(buf), want * hs))
        if name == 'sSurfacePool':
            g.write('sSurfacePoolSize', 'h', want)
        out[name] = want
        out[name + '_outside_image'] = True
    g._rh_arena = ImageArena(g, st.var('gSurfacesAllocated', 'i'))
    return out


def copy_values(st, a, host, t):
    prims, _p, _o = B.flatten(t)
    bn = a - 0x80000000
    for s, (no, ho) in prims.items():
        buf = st.rdn[bn + no[:, None] + np.arange(s)[None, :]][:, ::-1]
        dst = np.frombuffer((ctypes.c_uint8 * (int(ho.max()) + s)).from_address(host), np.uint8)
        dst[ho[:, None] + np.arange(s)[None, :]] = buf


def graft_global_pointers(g, st, tr, skip=('gObjectPool',)):
    bad = {}
    for a, sz, h, t, name in tr.regions:
        if name in skip or name in SKIP or name.startswith(SKIP_PREFIX) or sz > 0x40000:
            continue
        if SC_is_const(name):
            continue
        ok, nb = graft_pointers(g, st, tr, a, h, t, name)
        if nb:
            bad[name] = nb
    return bad


def SC_is_const(name):
    return name.startswith('bhv') or '_seg' in name


def graft_env_regions_romhack(g, st):
    pn = st.var('gEnvironmentRegions', 'I')
    if not pn:
        _write_q(g.addr('gEnvironmentRegions'), 0)
        return {'boxes': 0}
    n = st.get('h', pn)
    if not 0 <= n < 256:
        return f'unreadable box count {n} at {pn:08X}'
    vals = [st.get('h', pn + 2 * i) for i in range(1 + 6 * n)]
    _write_q(g.addr('gEnvironmentRegions'), _host_buffer(g, struct.pack(f'<{len(vals)}h', *vals)))
    return {'boxes': n, 'heights': [vals[6 * i + 6] for i in range(n)]}


def graft_env_regions(g, st):
    if 'gEnvironmentRegions' not in st.map:
        return 'unmapped'
    pn, ph = st.var('gEnvironmentRegions', 'I'), g.read('gEnvironmentRegions', 'Q')
    if not pn or not ph:
        return None if not pn and not ph else f'only on one side (st {pn:08X}, host {ph:X})'
    n = st.get('h', pn)
    if n != g.read(ph, 'h') or not 0 < n < 64:
        return f'different box counts (st {n}, host {g.read(ph, "h")})'
    data = struct.pack(f'<{1 + 6 * n}h', *(st.get('h', pn + 2 * i) for i in range(1 + 6 * n)))
    k32 = ctypes.windll.kernel32
    old = ctypes.c_uint32()
    k32.VirtualProtect(ctypes.c_void_p(ph), len(data), 0x04, ctypes.byref(old))
    changed = ctypes.string_at(ph, len(data)) != data
    ctypes.memmove(ph, data, len(data))
    return {'boxes': n, 'changed': changed, 'heights': [st.get('h', pn + 2 * (6 * i + 6)) for i in range(n)]}


OFF_COLLISION = [m for m in NM.members(O_TYPE) if m[3] == 'collisionData'][0][0]
OFF_RESPAWN_INFO = [m for m in NM.members(O_TYPE) if m[3] == 'respawnInfo'][0][0]
O_ANIMATIONS = 0x26
_SCRIPT_PTRS = {}


def script_ptrs(g, tr, host_bhv):
    if not host_bhv:
        return {}
    if host_bhv not in _SCRIPT_PTRS:
        out = {}
        item = next((x for x in tr.bhv.items if x[3] == host_bhv), None)
        if item:
            n = item[1] // 4
            words = struct.unpack(f'<{n}Q', ctypes.string_at(host_bhv, 8 * n))
            for j in range(n - 1):
                w = words[j] & 0xFFFFFFFF
                if w == 0x2A000000:
                    out.setdefault('collision', words[j + 1])
                elif w >> 16 == 0x2700 | O_ANIMATIONS:
                    out.setdefault('anims', words[j + 1])
        _SCRIPT_PTRS[host_bhv] = out
    return _SCRIPT_PTRS[host_bhv]


_KINDS = None


def obj_kinds():
    global _KINDS
    if _KINDS is None:
        import obj_fields
        _KINDS = obj_fields.behavior_kinds()
    return _KINDS


def graft_objects(g, st, tr, log=None):
    pool_n, pool_h = st.map['gObjectPool'], g.addr('gObjectPool')
    native = [ctypes.string_at(pool_h + i * O_HOST, O_HOST) for i in range(240)]
    t = O_TYPE
    prims, plist, _objptr = B.flatten(t)
    ptrs = _ptr_prims(t)
    bhv_off_n = [m for m in NM.members(t) if m[3] == 'behavior'][0][:2]
    by_bhv = {}
    for i in range(240):
        by_bhv.setdefault(struct.unpack_from('<Q', native[i], bhv_off_n[1])[0], []).append(i)
    kinds_all = obj_kinds()
    stats = {'donor': 0, 'miss': 0, 'rawptr': 0}
    for i in range(240):
        a, h = pool_n + i * O_N64, pool_h + i * O_HOST
        bn = a - 0x80000000
        for s, (no, ho) in prims.items():
            sel = (no < RAW_N) | (no >= RAW_N + 4 * N_RAW)
            no2, ho2 = no[sel], ho[sel]
            buf = st.rdn[bn + no2[:, None] + np.arange(s)[None, :]][:, ::-1]
            dst = np.frombuffer((ctypes.c_uint8 * O_HOST).from_address(h), np.uint8)
            dst[ho2[:, None] + np.arange(s)[None, :]] = buf
        bhv_v = st.get('I', a + bhv_off_n[0])
        bname, _ = tr.bhv.name(bhv_v)
        host_bhv = tr.to_host(bhv_v) if bhv_v else 0
        donors = [native[j] for j in by_bhv.get(host_bhv, [])]
        if host_bhv and struct.unpack_from('<Q', native[i], bhv_off_n[1])[0] == host_bhv:
            donors.insert(0, native[i])
        sp = script_ptrs(g, tr, host_bhv)
        for no, ho, kind in ptrs:
            v = st.get('I', a + no)
            if kind == 'pi' and not (0x80000000 <= v < 0x80800000):
                _write_q(h + ho, v)
                continue
            if no == OFF_COLLISION and v and tr.romhack:
                hp = tr.to_host(v, shift=False)
                if hp is None:
                    hp = tr.host_collision(v)
                    stats['collision_copy' if hp else 'collision_miss'] = \
                        stats.get('collision_copy' if hp else 'collision_miss', 0) + 1
                if hp is None:
                    tr.misses.setdefault(f'obj[{i}].collisionData', set()).add(v)
                    hp = sp.get('collision', 0)
            elif no == OFF_COLLISION and v and 'collision' in sp:
                hp = tr.to_host(v, shift=False)
                if hp is None:
                    hp = sp['collision']
            else:
                hp = tr.to_host(v, f'obj[{i}]+{no:X}')
                if (no == OFF_RESPAWN_INFO and v and (hp is None and tr.romhack or hp is not None and _const_target(g, hp))):
                    hc = tr.host_word(v)
                    if hc is not None:
                        hp = hc
                        tr.misses.pop(f'obj[{i}]+{no:X}', None)
            if hp is None:
                if donors:
                    hp = struct.unpack_from('<Q', donors[0], ho)[0]
                    stats['donor'] += 1
                else:
                    hp = 0
                    stats['miss'] += 1
            _write_q(h + ho, hp)
        kinds = kinds_all.get(bname, {})
        for k in range(N_RAW):
            w = st.rd[bn + RAW_N + 4 * k: bn + RAW_N + 4 * k + 4]
            v = struct.unpack('>I', w)[0]
            kind = kinds.get(k)
            ptr_like = 0x80000000 <= v < 0x80800000
            donor_ptr = next((struct.unpack_from('<Q', d, PTR_H + 8 * k)[0] for d in donors
                              if struct.unpack_from('<Q', d, PTR_H + 8 * k)[0]), 0)
            if k == O_ANIMATIONS and 'anims' in sp:
                kind, donor_ptr = 'ptr', sp['anims']
            segmented = 0x01000000 <= v < 0x20000000
            if kind is None:
                kind = ('s16' if k in NM.OBJ_S16_COMMON else
                        'ptr' if donor_ptr or ((ptr_like or segmented) and tr.to_host(v) is not None) else '32')
            if kind == 'ptr' and (v == 0 or ptr_like or donor_ptr or segmented):
                hp = tr.to_host(v, f'obj[{i}].raw[{k:X}]')
                if hp is None:
                    hp = donor_ptr
                    stats['donor' if hp else 'miss'] += 1
                _write_q(h + PTR_H + 8 * k, hp)
                ctypes.memmove(h + RAW_H + 4 * k, bytes(4), 4)
                stats['rawptr'] += 1
            else:
                hb = bytes([w[1], w[0], w[3], w[2]]) if kind == 's16' else w[::-1]
                ctypes.memmove(h + RAW_H + 4 * k, hb, 4)
                _write_q(h + PTR_H + 8 * k, 0)
    return stats


def graft_envfx(g, st):
    if 'gEnvFxBuffer' not in st.map:
        return 'unmapped'
    pn, ph = st.var('gEnvFxBuffer', 'I'), g.read('gEnvFxBuffer', 'Q')
    mode = st.var('gEnvFxMode', 'b')
    if not pn or not ph or not mode:
        return {'mode': mode, 'copied': 0}
    n = st.var('sBubbleParticleCount', 'i') if mode >= 10 else st.var('gSnowParticleMaxCount', 'h')
    hv = lambda name, f: g.read(g.base + var_info(name)['rva'], f)
    hn = hv('sBubbleParticleCount', 'i') if mode >= 10 else hv('gSnowParticleMaxCount', 'h')
    if n != hn or not 0 < n <= 200:
        return {'mode': mode, 'copied': 0, 'why': f'particle counts differ (st {n}, host {hn})'}
    t = NM.find_type('EnvFxParticle')
    ns, _, hs = NM.layout(t)[:3]
    for k in range(n):
        copy_values(st, pn + k * ns, ph + k * hs, t)
    return {'mode': mode, 'copied': n}


def reload_animations(g, st, tr):
    out = {}
    mario = g.read('gMarioObject', 'Q')
    anim_id_off = [m[1] for m in NM.members(NM.find_type('GraphNodeObject')) if m[3] == 'animInfo'][0]
    ai = {m[3]: m[1] for m in NM.members(NM.find_type('AnimInfo'))}
    an = {m[3]: m[1] for m in NM.members(NM.find_type('Animation'))}
    if mario:
        anim_id = g.read(mario, 'h', anim_id_off + ai['animID'])
        buf = g.addr('gMarioAnimsBuf')
        if anim_id >= 0:
            _write_q(buf + 8, 0)
            load = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int)(g.base + g.syms['load_patchable_table'])
            target = g.read(buf, 'Q', 0x10)
            if load(buf, anim_id):
                for f in ('values', 'index'):
                    _write_q(target + an[f], target + g.read(target, 'Q', an[f]))
            if st.get('I', st.map['gObjectPool'] + ((mario - g.addr('gObjectPool')) // O_HOST) * O_N64 + 0x3C):
                _write_q(mario + anim_id_off + ai['curAnim'], target)
            out['mario_anim'] = anim_id
    pool_n, pool_h = st.map['gObjectPool'], g.addr('gObjectPool')
    cur_n = [m[0] for m in NM.members(O_TYPE) if m[3] == 'header'][0] + 0x3C
    fixed = 0
    for i in range(240):
        h = pool_h + i * O_HOST
        if not g.read(h, 'h', 0xB4) & 1 or h == mario:
            continue
        v = st.get('I', pool_n + i * O_N64 + cur_n)
        anims = g.read(h, 'Q', PTR_H + 8 * O_ANIMATIONS)
        k = g.read(h, 'h', anim_id_off + ai['animID'])
        if not (v and 0 <= k < 64 and g.base <= anims < g.base + IMAGE_SIZE - 8 * 64):
            continue
        want = g.read(anims, 'Q', 8 * k)
        if not g.base <= want < g.base + IMAGE_SIZE:
            continue
        have = g.read(h, 'Q', anim_id_off + ai['curAnim'])
        if not want or want == have:
            continue
        if tr.to_host(v) is None:
            _write_q(h + anim_id_off + ai['curAnim'], want)
            fixed += 1
    out['object_curAnim'] = fixed
    return out


def _warp_with_fallback(g, info, rep):
    tries = [(info['warp_node'], info['act'])]
    for node, act in ((0x0A, info['act'] or 1), (0x0A, 1), (0x00, 1)):
        if (node, act) not in tries:
            tries.append((node, act))
    err = None
    for node, act in tries:
        try:
            n = warp_native(g, info['level'], info['area'], node, act, info['save_file'], None)
            if (node, act) != tries[0]:
                rep['warp_fallback'] = {'node': node, 'act': act, 'first_error': err}
            return n
        except (OSError, RuntimeError) as e:
            err = f'{type(e).__name__}: {e}'
    raise RuntimeError(f"warp to level {info['level']} area {info['area']}: no working entry ({err})")


def load_st(g, path, patches='auto', log=None, romhack=False, rom=None):
    import st_patches
    import st_romhack
    st_patches.revert(g)
    st_romhack.revert(g)
    if getattr(g, '_rh_levels', None):
        g._rh_levels.uninstall()
        g._rh_levels = None
    st = St(path)
    info = st.info()
    rep = {'st': info}
    try:
        rep['warp_frames'] = _warp_with_fallback(g, info, rep)
    except RuntimeError as e:
        if not (romhack and rom):
            raise
        amap = _area_remap(g, st, rom, info, log)
        rep['area_remap'] = amap
        lv0 = install_rom_levels(g, st, Translator(g, st, romhack=True), rom, log, area_map=amap)
        rep['warp_after_hack_levels'] = {'first_error': str(e), 'levels': lv0['levels']}
        rep['warp_frames'] = _warp_with_fallback(g, info, rep)
    pool_size = g.read('sSurfacePoolSize', 'h')
    copy_values(st, st.map['gSaveBuffer'], g.addr('gSaveBuffer'), var_info('gSaveBuffer')['type'])
    rep['values'] = len(graft_values(g, st))
    if romhack:
        g.write('sSurfacePoolSize', 'h', pool_size)
        rep['pools'] = ensure_pools(g, st)
    tr = Translator(g, st, romhack=romhack)
    register_heap(g, st, tr)
    rep['heap'] = graft_heap(g, st, tr)
    rep['env_regions'] = graft_env_regions_romhack(g, st) if romhack else graft_env_regions(g, st)
    rep['envfx'] = graft_envfx(g, st)
    rep['objects'] = graft_objects(g, st, tr)
    skip = ('gObjectPool', 'gEnvironmentRegions') if romhack else ('gObjectPool',)
    rep['global_pointers_unknown'] = graft_global_pointers(g, st, tr, skip)
    rep['animations'] = reload_animations(g, st, tr)
    if romhack:
        rep['romhack'] = romhack_checks(g, st, tr)
    rep['unknown_pointers'] = {k: sorted(hex(x) for x in v) for k, v in tr.misses.items()}
    det = st_patches.detect(st.rd)
    names = det['known'] if patches == 'auto' else (list(patches) if patches else [])
    rep['patches'] = dict(det, applied=[n for n, _ in st_patches.apply(g, names)] and names)
    if rep['patches']['applied']:
        (log or print)(f'[st_load] code patches of the .st reproduced in the DLL: {rep["patches"]["applied"]}')
    if det['object_edits'] and patches:
        rep['object_edits'] = st_patches.install_object_edits(g, st, tr, det['object_edits'])
        (log or print)(f'[st_load] object edits of the .st payload reproduced: {rep["object_edits"]}')
    if romhack:
        rh = st_romhack.detect(st.rd)
        for name in rh:
            st_romhack.apply(g, name, rd=st.rd)
        rep['romhack']['code_patches_reproduced'] = rh
        rep['romhack']['dialogs_replaced'] = st_romhack.install_dialogs(g, st)
        if rom:
            if getattr(g, '_rh_levels', None):
                g._rh_levels.uninstall()
                g.__dict__.setdefault('_rh_old', []).append(g._rh_levels)
                g._rh_levels = None
            rep['romhack']['levels'] = install_rom_levels(g, st, tr, rom, log, area_map=rep.get('area_remap'))
            rep['romhack']['warp_nodes'] = graft_warp_nodes(g, st, tr)
            if 'island_2d' in rh:
                rep['romhack']['areas_2d'] = st_romhack.island_area_flags(g)
            am =(rep.get('area_remap') or {}).get(g.read('sWarpDest', 'B', 1)) or {}
            if g.read('sWarpDest', 'B', 0) and g.read('sWarpDest', 'B', 2) in am:
                g.write('sWarpDest', 'B', am[g.read('sWarpDest', 'B', 2)], 2)
                rep['area_remap_warp'] = dict(am)
            lv = rep['romhack']['levels']
            n = len(lv['levels'])
            rep['romhack']['level_scripts'] = (f"hack level scripts installed ({n}), transitions reproduced"
                                               + (f"; not installed (stay vanilla): {sorted(lv['failed'])}" if lv['failed'] else ''))
            pt = rep['romhack'].get('pending_transition')
            if pt:
                pt['why'] = (f"the .st is inside a warp to level {pt['to_level']}: "
                             + ("its hack level script is installed, the transition is reproduced"
                                if pt['to_level'] in lv['levels'] else
                                "that level is not installed (vanilla data will be loaded)"))
        rep['romhack']['code_words_differing_from_us'] = len(det['hooks']) + len(det['unsupported'])
        if rh:
            (log or print)(f'[st_load] ROM-hack code patches reproduced in the DLL: {rh}')
    elif det['unsupported'] or det['unexplained_hooks']:
        msg = f'.st code patches not reproduced: {det["unsupported"] or det["hooks"]}'
        rep['warnings'] = [msg]
        (log or print)('[st_load] WARNING ' + msg)
    if romhack and RESUME_ACT_SELECT_EXIT:
        rep['act_select_exit'] = resume_act_select_exit(g, st)
    g.history = []
    return rep


def _area_remap(g, st, rom, info, log=None):
    sys.path.insert(0, os.path.join(HERE, 'romhack'))
    import rh_level
    try:
        sa = rh_level.RomhackLevels(g, st, rom, None, log=log).script_areas(info['level'])
    except Exception:
        return {}
    if info['area'] not in sa and len(set(sa)) == 1:
        return {info['level']: {sa[0]: info['area']}}
    return {}


def graft_warp_nodes(g, st, tr):
    A = {m[3]: m[:2] for m in NM.members(NM.find_type('Area'))}
    W = {m[3]: m[:2] for m in NM.members(NM.find_type('ObjectWarpNode'))}
    k = NM.find_type('ObjectWarpNode')
    size = NM.T[k]['size']
    area_n = st.get('I', st.map['gCurrentArea'])
    area_h = g.read('gCurrentArea', 'Q')
    if not (area_n and area_h):
        return 0
    keep, prev, n, head = [], None, 0, 0
    p = st.get('I', area_n + A['warpNodes'][0])
    while p and n < 64:
        buf = ctypes.create_string_buffer(size)
        a = ctypes.addressof(buf)
        ctypes.memmove(a + W['node'][1], bytes(st.rd[p - 0x80000000 + W['node'][0]:p - 0x80000000 + W['node'][0] + 4]), 4)
        obj = st.get('I', p + W['object'][0])
        if obj:
            ctypes.c_void_p.from_address(a + W['object'][1]).value = tr.to_host(obj) or 0
        if prev is None:
            head = a
        else:
            ctypes.c_void_p.from_address(prev + W['next'][1]).value = a
        keep.append(buf)
        prev, n = a, n + 1
        p = st.get('I', p + W['next'][0])
    if n:
        g.write(area_h, 'Q', head, A['warpNodes'][1])
    pw = st.get('I', area_n + A['paintingWarpNodes'][0])
    if pw:
        o = pw - 0x80000000
        buf = ctypes.create_string_buffer(bytes(st.rd[o:o + 4 * 45]), 4 * 45)
        keep.append(buf)
        g.write(area_h, 'Q', ctypes.addressof(buf), A['paintingWarpNodes'][1])
    g.__dict__.setdefault('_rh_warp_bufs', []).append(keep)
    return n


def install_rom_levels(g, st, tr, rom, log=None, area_map=None):
    sys.path.insert(0, os.path.join(HERE, 'romhack'))
    import rh_level
    lv = rh_level.RomhackLevels(g, st, rom, tr, log=log, area_map=area_map)
    done, failed = [], {}
    for level in range(1, 39):
        try:
            lv.level_entry(level)
        except KeyError:
            continue
        try:
            lv.install(level)
            done.append(level)
        except Exception as ex:
            failed[level] = f'{type(ex).__name__}: {ex}'
    g._rh_levels = lv
    regs = g.__dict__.setdefault('_rh_regions', [])
    regs += [(ctypes.addressof(b), ctypes.sizeof(b)) for b in lv.keep if (ctypes.addressof(b), ctypes.sizeof(b)) not in regs]
    return dict(lv.report(), levels=done, failed=failed)


RESUME_ACT_SELECT_EXIT = True
N64_SCURRENTCMD, N64_SDELAYFRAMES = 0x8038BE28, 0x8038B8A4


def resume_act_select_exit(g, st):
    u32 = lambda a: st.get('I', a)
    try:
        cmd = u32(N64_SCURRENTCMD)
        if not (0x80000000 <= cmd < 0x80800000) or st.var('sCurrPlayMode', 'h') != PLAY_MODE_CHANGE_LEVEL:
            return None
        if not (u32(cmd) >> 16 == 0x0304 and u32(cmd - 8) >> 16 == 0x3308 and u32(cmd + 4) >> 16 == 0x1C04):
            return None
        left = st.get('H', N64_SDELAYFRAMES)
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}
    if left < 3:
        return {'frames_left': left, 'why': 'too few frames left to reproduce'}
    lca = g.base + var_info('levelCommandAddr')['rva']
    cur = g.read(lca, 'Q')
    c, out = cur, b''
    for _ in range(16):
        if ctypes.string_at(c, 1)[0] in (0x03, 0x04):
            break
        n = ctypes.string_at(c + 1, 1)[0] * 2
        out += ctypes.string_at(c, n)
        c += n
    else:
        return {'error': 'no pause in the DLL level script'}
    buf = ctypes.create_string_buffer(out + bytes([0x03, 0x04]) + struct.pack('<h', left - 2) + bytes(4)
                                      + bytes([0x05, 0x08]) + bytes(6) + struct.pack('<Q', c))
    g.__dict__.setdefault('_rh_keep_bufs', []).append(buf)
    g.write(lca, 'Q', ctypes.addressof(buf))
    g.write('sWarpCheckpointActive', 'B', 2)
    return {'frames_left': left, 'sleep': left - 2, 'act_select': 'skipped once (chosen before the .st)'}


def romhack_checks(g, st, tr):
    pool_n, pool_h = st.map['gObjectPool'], g.addr('gObjectPool')
    mem = {m[3]: m[:2] for m in NM.members(O_TYPE)}
    act_n, act_h = mem['activeFlags']
    bhv_n = mem['behavior'][0]
    brk = (tr.bhv.by_name.get('bhvSpinAirborneWarp') or (None, None, None, None))[3]
    deact = []
    for i in range(240):
        a = pool_n + i * O_N64
        if not st.get('h', a + act_n) & 1:
            continue
        b = st.get('I', a + bhv_n)
        if tr.bhv.to_host(b) is None and getattr(tr.bhv, 'translate_at', None) and tr.bhv.translate_at(b):
            o_h = pool_h + i * O_HOST
            g.write(o_h, 'Q', tr.bhv.to_host(b), mem['behavior'][1])
            cur = tr.bhv.to_host(st.get('I', a + mem['curBhvCommand'][0]))
            g.write(o_h, 'Q', cur or tr.bhv.to_host(b), mem['curBhvCommand'][1])
            if not cur:
                g.write(o_h, 'I', 0, mem['bhvStackIndex'][1])
            for k in range(8):
                h = tr.bhv.to_host(st.get('I', a + mem['bhvStack'][0] + 4 * k))
                if h:
                    g.write(o_h, 'Q', h, mem['bhvStack'][1] + 8 * k)
            continue
        if tr.bhv.to_host(b) is None:
            o_h = pool_h + i * O_HOST
            g.write(o_h, 'h', 0, act_h)
            if brk:
                for f_ in ('behavior', 'curBhvCommand'):
                    g.write(o_h, 'Q', brk, mem[f_][1])
                g.write(o_h, 'I', 0, mem['bhvStackIndex'][1])
            deact.append((i, hex(b)))
    pending = None
    if st.var('sCurrPlayMode', 'h') == PLAY_MODE_CHANGE_LEVEL or st.var('sWarpDest', 'B', 0):
        pending = {'play_mode': st.var('sCurrPlayMode', 'h'), 'warp_type': st.var('sWarpDest', 'B', 0),
                   'to_level': st.var('sWarpDest', 'B', 1), 'to_area': st.var('sWarpDest', 'B', 2),
                   'to_node': st.var('sWarpDest', 'B', 3),
                   'why': 'the .st is inside a warp: the next area is built by the vanilla level script, not the '
                          'hack\'s -> nothing after the transition is reproducible; take the .st after it'}
    return {'pending_transition': pending,
            'objects_deactivated_unknown_behavior': deact,
            'vanilla_behaviors_not_found': tr.bhv.missing,
            'host_copies': {k: sum(1 for _, w in tr.host_copies.values() if w == k) for k in ('collision', 'spawn word')},
            'host_copies_bytes_in_image': getattr(getattr(g, '_rh_arena', None), 'used', 0),
            'buffers_outside_dll_image': getattr(g, '_rh_outside', 0),
            'level_scripts': 'the DLL runs the vanilla US level scripts: any warp / level or area change / death / '
                             'star exit during the movie loads vanilla data, not the hack\'s (not reproducible)'}


def compare_game(g, path):
    m = n64_map()
    diffs = [n for n in SB.compare(g, path) if n in m]
    if 'gObjectPool' in diffs:
        st = St(path)
        v = var_info('gObjectPool')
        sec, off = _host_loc(v['rva'])
        mem = g.save()[0]
        img, ok = B.n64_image(v['type'], np.frombuffer(mem[0 if sec == '.data' else 1], np.uint8), off)
        a = st.map['gObjectPool'] - 0x80000000
        bad = np.nonzero(ok & (img != st.rdn[a:a + len(img)]))[0]
        active = {i for i in range(240) if st.get('h', st.map['gObjectPool'] + i * O_N64 + 0x74) & 1}
        if not any(int(x) // O_N64 in active for x in bad):
            diffs.remove('gObjectPool')
    return diffs


def _normalized(g, raw):
    q = np.frombuffer(raw, np.uint64).copy()
    m = (q >= g.base) & (q < g.base + IMAGE_SIZE)
    q[m] -= np.uint64(g.base)
    return q.view(np.uint8)


def host_diff(g1, g2, limit=None):
    m1, m2 = g1.save()[0], g2.save()[0]
    out = []
    for sec, a, b in (('.data', m1[0], m2[0]), ('.bss', m1[1], m2[1])):
        x, y = _normalized(g1, a), _normalized(g2, b)
        va = _SECS[sec][0]
        idx = np.nonzero(x != y)[0]
        if not len(idx):
            continue
        global _VARS
        var_info('gMarioStates')
        spans = sorted((v['rva'] - va, NM.layout(v['type'])[2] if v['type'] else 1, k)
                       for k, v in _VARS.items() if _host_loc(v['rva'])[0] == sec)
        starts = np.array([s[0] for s in spans])
        hit = {}
        for i in idx:
            j = int(np.searchsorted(starts, i, 'right')) - 1
            key = spans[j][2] if j >= 0 and i < spans[j][0] + max(spans[j][1], 1) else f'{sec}+{i:X}'
            hit[key] = hit.get(key, 0) + 1
        out.extend(sorted(hit.items(), key=lambda kv: -kv[1]))
    return out[:limit] if limit else out
