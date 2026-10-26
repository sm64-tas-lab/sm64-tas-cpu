import struct
import sys

LVL = {
    0x00: 'EXECUTE', 0x01: 'EXIT_AND_EXECUTE', 0x02: 'EXIT', 0x03: 'SLEEP', 0x04: 'SLEEP_BEFORE_EXIT', 0x05: 'JUMP',
    0x06: 'JUMP_LINK', 0x07: 'RETURN', 0x08: 'JUMP_LINK_PUSH_ARG', 0x09: 'JUMP_N_TIMES', 0x0A: 'LOOP_BEGIN',
    0x0B: 'LOOP_UNTIL', 0x0C: 'JUMP_IF', 0x0D: 'JUMP_LINK_IF', 0x0E: 'SKIP_IF', 0x0F: 'SKIP', 0x10: 'SKIP_NOP',
    0x11: 'CALL', 0x12: 'CALL_LOOP', 0x13: 'SET_REG', 0x14: 'PUSH_POOL', 0x15: 'POP_POOL', 0x16: 'FIXED_LOAD',
    0x17: 'LOAD_RAW', 0x18: 'LOAD_MIO0', 0x19: 'LOAD_MARIO_HEAD', 0x1A: 'LOAD_MIO0_TEXTURE', 0x1B: 'INIT_LEVEL',
    0x1C: 'CLEAR_LEVEL', 0x1D: 'ALLOC_LEVEL_POOL', 0x1E: 'FREE_LEVEL_POOL', 0x1F: 'AREA', 0x20: 'END_AREA',
    0x21: 'LOAD_MODEL_FROM_DL', 0x22: 'LOAD_MODEL_FROM_GEO', 0x23: 'CMD23', 0x24: 'OBJECT', 0x25: 'MARIO',
    0x26: 'WARP_NODE', 0x27: 'PAINTING_WARP_NODE', 0x28: 'INSTANT_WARP', 0x29: 'LOAD_AREA', 0x2A: 'UNLOAD_AREA',
    0x2B: 'MARIO_POS', 0x2C: 'UNLOAD_MARIO_AREA', 0x2D: 'UPDATE_OBJECTS', 0x2E: 'TERRAIN', 0x2F: 'ROOMS',
    0x30: 'SHOW_DIALOG', 0x31: 'TERRAIN_TYPE', 0x32: 'NOP', 0x33: 'TRANSITION', 0x34: 'BLACKOUT', 0x35: 'GAMMA',
    0x36: 'SET_BACKGROUND_MUSIC', 0x37: 'SET_MENU_MUSIC', 0x38: 'STOP_MUSIC', 0x39: 'MACRO_OBJECTS', 0x3A: 'CMD3A',
    0x3B: 'WHIRLPOOL', 0x3C: 'GET_OR_SET',
}


class Mem:

    def __init__(self, rd, seg_base=None, segs=None):
        self.rd, self.seg_base, self.segs = rd, seg_base or {}, segs or {}

    def phys(self, a):
        if 0x80000000 <= a < 0x80000000 + len(self.rd):
            return ('rd', a - 0x80000000)
        s, o = a >> 24, a & 0xFFFFFF
        if s in self.segs:
            return (s, o)
        if s in self.seg_base:
            return ('rd', self.seg_base[s] + o)
        raise KeyError(f'segment {s:02X} not mapped ({a:08X})')

    def buf(self, a):
        k, o = self.phys(a)
        return (self.rd if k == 'rd' else self.segs[k]), o

    def u8(self, a):
        b, o = self.buf(a)
        return b[o]

    def u16(self, a):
        b, o = self.buf(a)
        return struct.unpack_from('>H', b, o)[0]

    def s16(self, a):
        b, o = self.buf(a)
        return struct.unpack_from('>h', b, o)[0]

    def u32(self, a):
        b, o = self.buf(a)
        return struct.unpack_from('>I', b, o)[0]

    def bytes(self, a, n):
        b, o = self.buf(a)
        return bytes(b[o:o + n])


def from_st(path):
    sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
    import st_load as L
    st = L.St(path)
    seg = {s: st.get('I', st.all['sSegmentTable'] + 4 * s) for s in range(32)}
    return Mem(st.rd, {s: b for s, b in seg.items() if b}), st


def dump_level(m, a, n=400):
    out = []
    for _ in range(n):
        cid, size = m.u8(a), m.u8(a + 1)
        words = [m.u32(a + 4 * i) for i in range(max(1, size // 4))]
        out.append((a, cid, LVL.get(cid, f'?{cid:02X}'), words))
        if cid in (0x02, 0x07) or size == 0:
            break
        a += size
    return out


if __name__ == '__main__':
    m, st = from_st(sys.argv[1])
    a = int(sys.argv[2], 16) if len(sys.argv) > 2 else None
    if a is None:
        print('segments:', {f'{k:02X}': f'{v:08X}' for k, v in m.seg_base.items()})
        sys.exit()
    for addr, cid, name, words in dump_level(m, a):
        print(f'{addr:08X} {name:22s} ' + ' '.join(f'{w:08X}' for w in words))


GEO = {0x00: 'BRANCH_AND_LINK', 0x01: 'END', 0x02: 'BRANCH', 0x03: 'RETURN', 0x04: 'OPEN_NODE', 0x05: 'CLOSE_NODE',
       0x06: 'ASSIGN_AS_VIEW', 0x07: 'UPDATE_NODE_FLAGS', 0x08: 'NODE_ROOT', 0x09: 'NODE_ORTHO', 0x0A: 'CAMERA_FRUSTUM',
       0x0B: 'NODE_START', 0x0C: 'ZBUFFER', 0x0D: 'RENDER_RANGE', 0x0E: 'SWITCH_CASE', 0x0F: 'CAMERA',
       0x10: 'TRANSLATE_ROTATE', 0x11: 'TRANSLATE', 0x12: 'ROTATE', 0x13: 'ANIMATED_PART', 0x14: 'BILLBOARD',
       0x15: 'DISPLAY_LIST', 0x16: 'SHADOW', 0x17: 'RENDER_OBJ', 0x18: 'ASM', 0x19: 'BACKGROUND', 0x1A: 'NOP_1A',
       0x1C: 'HELD_OBJECT', 0x1D: 'SCALE', 0x1E: 'NOP_1E', 0x1F: 'NOP_1F', 0x20: 'CULLING_RADIUS'}


def geo_fields(m, a):
    c, p = m.u8(a), m.u8(a + 1)
    if c in (0x00,):
        return ['BBH', 'PTR:geo']
    if c == 0x02:
        return ['BBH', 'PTR:geo']
    if c in (0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x09, 0x0B, 0x0C, 0x17, 0x20):
        return ['BBH']
    if c == 0x08:
        return ['BBH', 'HH', 'HH']
    if c == 0x0A:
        return ['BBH', 'HH'] + (['PTR:func'] if p else [])
    if c == 0x0D:
        return ['BBH', 'HH']
    if c == 0x0E:
        return ['BBH', 'PTR:func']
    if c == 0x0F:
        return ['BBH', 'HH', 'HH', 'HH', 'PTR:func']
    if c == 0x10:
        n = {0: 3, 1: 1, 2: 1, 3: 0}[(p & 0x70) >> 4]
        return ['BBH'] + ['HH'] * n + (['PTR:dl'] if p & 0x80 else [])
    if c in (0x11, 0x12, 0x14):
        return ['BBH', 'HH'] + (['PTR:dl'] if p & 0x80 else [])
    if c == 0x13:
        return ['BBH', 'HH', 'PTR:dl']
    if c == 0x15:
        return ['BBH', 'PTR:dl']
    if c == 0x16:
        return ['BBH', 'HH']
    if c == 0x18:
        return ['BBH', 'PTR:func']
    if c == 0x19:
        return ['BBH', 'PTR:func']
    if c == 0x1A:
        return ['BBH', 'HH']
    if c == 0x1C:
        return ['BBH', 'HH', 'HH', 'PTR:func']
    if c == 0x1D:
        return ['BBH', 'W'] + (['PTR:dl'] if p & 0x80 else [])
    if c == 0x1E:
        return ['BBH', 'HH']
    if c == 0x1F:
        return ['BBH', 'HH', 'HH', 'HH']
    raise ValueError(f'unknown geo command {c:02X} at {a:08X}')


def dump_geo(m, a, n=600):
    out = []
    for _ in range(n):
        f = geo_fields(m, a)
        words = [m.u32(a + 4 * i) for i in range(len(f))]
        out.append((a, m.u8(a), GEO.get(m.u8(a)), f, words))
        if m.u8(a) in (0x01, 0x03) or (m.u8(a) == 0x02 and m.u8(a + 1) == 0):
            break
        a += 4 * len(f)
    return out
