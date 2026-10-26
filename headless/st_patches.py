import ctypes
import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
VANILLA = os.path.join(os.path.dirname(HERE), 'MOVIE_0STAR', '00_reset.st')
CODE_RANGES = ((0x80246000, 0x8032D560), (0x80378800, 0x80385F90))
EXP_RAM = (0x80400000, 0x80410000)

KNOWN = {
    'task6_hidden_blue_coins_never_expire': {
        'targets': {0x802C23E0, 0x802C23F4},
        'host': [('bhv_hidden_blue_coin_loop', 'cur_obj_wait_then_blink', b'\x31\xC0\x90\x90\x90', 1)],
    },
    'task6_blue_coin_switch_tick_silent': {
        'targets': {0x802C25BC, 0x802C25D8},
        'host': [('bhv_blue_coin_switch_loop', 'play_sound', b'\x90' * 5, 2)],
    },
    'task11_ccm_touched_star_no_spawn': {
        'targets': {0x802B23B4},
        'host': [('bhv_ccm_touched_star_spawn_loop', 'spawn_default_star', b'\x90' * 5, 1)],
    },
}

FIND_NEAREST = 0x8029F95C
MARK_DELETE = 0x802CA028
OBJ_POS = {0xA0: 0, 0xA4: 1, 0xA8: 2}
DONE_FLAG = 'gAudioUnusedBuffer'


def _words(rd, a, b):
    return struct.unpack(f'>{(b - a) // 4}I', rd[a - 0x80000000:b - 0x80000000])


def _nop_targets(rd):
    w = _words(rd, *EXP_RAM)
    regs, out = {}, set()
    for x in w:
        op = x >> 26
        rs, rt, imm = (x >> 21) & 31, (x >> 16) & 31, x & 0xFFFF
        if op == 0x0F:
            regs[rt] = imm << 16
        elif op == 0x0D and rs in regs:
            regs[rt] = regs[rs] | imm
        elif op == 0x09 and rs in regs:
            regs[rt] = (regs[rs] + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF
        elif op == 0x23 and rs in regs:
            a = (regs[rs] + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF
            regs[rt] = regs.get(('mem', a))
            if regs[rt] is None:
                regs.pop(rt)
        elif op == 0x2B and rs in regs:
            a = (regs[rs] + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF
            if rt == 0:
                out.add(a)
            elif rt in regs:
                regs[('mem', a)] = regs[rt]
    return {a for a in out if any(lo <= a < hi for lo, hi in CODE_RANGES)}


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def object_edits(rd):
    lo = EXP_RAM[0]
    w = _words(rd, *EXP_RAM)
    regs, fregs = {0: 0}, {}
    finds, back = [], []
    root = done_var = None
    i = 0
    while i < len(w):
        pc, x = lo + 4 * i, w[i]
        op, rs, rt, imm = x >> 26, (x >> 21) & 31, (x >> 16) & 31, x & 0xFFFF
        if op == 0 and x & 0x3F == 0x08 and rs == 31:
            break
        if op == 0x03:
            i += 1
            _exec(w[i], regs, fregs, rd)
            tgt = 0x80000000 | (x & 0x3FFFFFF) << 2
            if tgt == FIND_NEAREST and 4 in regs:
                finds.append({'pc': pc, 'bhv': regs[4], 'delete': False, 'pos': {}})
            elif tgt == MARK_DELETE and finds:
                finds[-1]['delete'] = True
            regs, fregs = {0: 0}, {}
        elif op in (0x04, 0x05):
            k = [regs.get(r) for r in (rs, rt)]
            c = [v for v in k if v is not None and 0x80000000 <= v < 0x80800000]
            if c and None in k and not finds and root is None:
                root = c[0]
            tgt = pc + 4 + 4 * _s16(imm)
            if tgt < pc:
                back.append((pc, tgt))
        elif op == 0x2B and rs in regs and regs.get(rt) == 1 and finds:
            a = (regs[rs] + _s16(imm)) & 0xFFFFFFFF
            if EXP_RAM[0] <= a < EXP_RAM[1]:
                done_var = a
        elif op == 0x39 and imm in OBJ_POS and finds and rt in fregs:
            finds[-1]['pos'][OBJ_POS[imm]] = fregs[rt]
        else:
            _exec(x, regs, fregs, rd)
        i += 1
    if not finds:
        return None
    ops, k = [], 0
    while k < len(finds):
        j = k
        while j + 1 < len(finds) and finds[j + 1]['bhv'] == finds[k]['bhv']:
            j += 1
        run = finds[k:j + 1]
        loop = any(run[0]['pc'] <= t <= run[-1]['pc'] for _, t in back)
        for f in ([run[0]] if loop else run):
            if loop and any(r['delete'] for r in run):
                ops.append({'bhv': f['bhv'], 'kind': 'delete_all', 'pos': None})
            elif f['delete']:
                ops.append({'bhv': f['bhv'], 'kind': 'delete', 'pos': None})
            elif len(f['pos']) == 3:
                ops.append({'bhv': f['bhv'], 'kind': 'move', 'pos': tuple(f['pos'][a] for a in range(3))})
        k = j + 1
    return {'root': root, 'done_var': done_var, 'ops': ops}


def _exec(x, regs, fregs, rd):
    op, rs, rt, imm = x >> 26, (x >> 21) & 31, (x >> 16) & 31, x & 0xFFFF
    if op == 0x0F:
        regs[rt] = imm << 16
    elif op == 0x0D:
        if rs in regs:
            regs[rt] = regs[rs] | imm
        else:
            regs.pop(rt, None)
    elif op == 0x09:
        if rs in regs:
            regs[rt] = (regs[rs] + _s16(imm)) & 0xFFFFFFFF
        else:
            regs.pop(rt, None)
    elif op == 0x31 and rs in regs:
        a = (regs[rs] + _s16(imm)) & 0xFFFFFFFF
        if 0x80000000 <= a < 0x80000000 + len(rd) - 4:
            fregs[rt] = struct.unpack('>f', rd[a - 0x80000000:a - 0x7FFFFFFC])[0]
    elif op == 0x11 and rs == 4:
        fs = (x >> 11) & 31
        if rt in regs:
            fregs[fs] = struct.unpack('>f', struct.pack('>I', regs[rt]))[0]
        else:
            fregs.pop(fs, None)
    elif op in (0x20, 0x21, 0x23, 0x24, 0x25):
        regs.pop(rt, None)
    elif op == 0 and (x >> 11) & 31:
        regs.pop((x >> 11) & 31, None)
    regs[0] = 0


class ObjectEdits:

    def __init__(self, g, st, tr, dec):
        import n64mem as NM
        t_o, t_a = NM.find_type('Object'), NM.find_type('Area')
        m = {x[3]: x[1] for x in NM.members(t_o)}
        self.o_size, self.o_active, self.o_raw, self.o_bhv = NM.layout(t_o)[2], m['activeFlags'], m['rawData'], m['behavior']
        a_n = NM.layout(t_a)[0]
        ga = st.map['gAreaData']
        self.level = st.var('gCurrLevelNum')
        self.area = None
        for k in range(8):
            if dec['root'] is None or st.get('I', ga + a_n * k + 4) == dec['root']:
                self.area = k
                break
        self.ops = []
        for o in dec['ops']:
            ram = tr.ram(o['bhv'])
            host = tr.bhv.to_host(ram) if ram is not None else None
            if host is None:
                raise RuntimeError(f'payload object edit: behavior {o["bhv"]:#x} not found in the DLL')
            self.ops.append((o['kind'], host, o['pos']))
        self.flag = g.addr(DONE_FLAG)
        done = dec['done_var'] is not None and st.get('I', dec['done_var']) != 0
        ctypes.c_int.from_address(self.flag).value = 1 if done else 0

    def __call__(self, g):
        if ctypes.c_int.from_address(self.flag).value:
            return
        if g.read('gCurrLevelNum', 'h') != self.level or g.read('gCurrAreaIndex', 'h') != self.area:
            return
        pool, cur = g.addr('gObjectPool'), g.read('gCurrentObject', 'Q')
        for kind, bhv, pos in self.ops:
            objs = [pool + self.o_size * i for i in range(240)]
            objs = [o for o in objs if ctypes.c_short.from_address(o + self.o_active).value
                    and ctypes.c_void_p.from_address(o + self.o_bhv).value == bhv and o != cur]
            if not objs:
                continue
            if kind != 'delete_all':
                objs = [min(objs, key=lambda o: self._dist2(o, cur))]
            for o in objs:
                if kind == 'move':
                    ctypes.memmove(o + self.o_raw + 4 * 6, struct.pack('<3f', *pos), 12)
                else:
                    ctypes.c_short.from_address(o + self.o_active).value = 0
        ctypes.c_int.from_address(self.flag).value = 1

    def _dist2(self, o, cur):
        if not cur:
            return 0
        a = struct.unpack('<3f', ctypes.string_at(o + self.o_raw + 4 * 6, 12))
        b = struct.unpack('<3f', ctypes.string_at(cur + self.o_raw + 4 * 6, 12))
        return sum((p - q) ** 2 for p, q in zip(a, b))


def install_object_edits(g, st, tr, dec):
    ed = ObjectEdits(g, st, tr, dec)
    g.frame_hooks.append(ed)
    return {'level': ed.level, 'area': ed.area, 'already_done': bool(ctypes.c_int.from_address(ed.flag).value),
            'ops': [(o['kind'], hex(o['bhv']), o['pos']) for o in dec['ops']]}


_ROM_REF = {}

# where the RAM's code ranges come from in the US ROM (main segment, engine segment): byte for byte the same as a
# vanilla savestate's RAM there (checked against MOVIE_0STAR/00_reset.st), so no savestate has to ship with the repo
ROM_CODE = ((0x80246000, 0x1000), (0x80378800, 0xF5580))


def vanilla_reference(vanilla=VANILLA):
    """the vanilla RAM's code: the reference savestate when there is one, else built from the user's US ROM"""
    import st_bridge as SB
    if os.path.isfile(vanilla):
        return SB.rdram(vanilla)
    if 'ref' not in _ROM_REF:
        import hl_paths
        import rom_lock
        rom = rom_lock.rom_z64(open(hl_paths.ROM, 'rb').read())
        ref = bytearray(8 << 20)
        for (lo, hi), (ram, off) in zip(CODE_RANGES, ROM_CODE):
            n = hi - lo
            ref[ram & 0x7FFFFF:(ram & 0x7FFFFF) + n] = rom[off:off + n]
        _ROM_REF['ref'] = bytes(ref)
    return _ROM_REF['ref']


def detect(st_rd, vanilla=VANILLA):
    ref = vanilla_reference(vanilla)
    hooks = []
    for lo, hi in CODE_RANGES:
        a_, b_ = _words(st_rd, lo, hi), _words(ref, lo, hi)
        hooks += [(lo + 4 * i, y, x) for i, (x, y) in enumerate(zip(a_, b_)) if x != y]
    payload = any(_words(st_rd, *EXP_RAM))
    targets = _nop_targets(st_rd) if payload else set()
    known = [k for k, v in KNOWN.items() if v['targets'] <= targets]
    covered = set().union(*(KNOWN[k]['targets'] for k in known)) if known else set()
    unsupported = sorted(targets - covered)
    edits = object_edits(st_rd) if payload else None
    return {'hooks': [(hex(a), f'{v:08X}', f'{s:08X}') for a, v, s in hooks], 'payload': payload,
            'nop_targets': sorted(hex(t) for t in targets), 'known': known,
            'unsupported': [hex(t) for t in unsupported],
            'object_edits': edits,
            'unexplained_hooks': bool(hooks) and not known and not edits}


_FUNCS = None


def _funcs():
    global _FUNCS
    if _FUNCS is None:
        from sm64hl import _SYMS
        fs = json.load(open(os.path.join(HERE, '_vars_cache.json')))['funcs']
        _FUNCS = {f['n']: f['rva'] for f in fs}
        for n, r in _SYMS.items():
            _FUNCS.setdefault(n, r)
        _FUNCS['_sorted'] = sorted({f['rva'] for f in fs} | {r for r in _SYMS.values() if r < 0xED000})
    return _FUNCS


def _calls_to(g, fn, callee):
    F = _funcs()
    start = F[fn]
    import bisect
    s = F['_sorted']
    end = s[bisect.bisect_right(s, start)]
    code = ctypes.string_at(g.base + start, end - start)
    tgt = F[callee]
    out = []
    for i in range(len(code) - 5):
        if code[i] == 0xE8 and start + i + 5 + struct.unpack_from('<i', code, i + 1)[0] == tgt:
            out.append(start + i)
    return out


def apply(g, names):
    k32 = ctypes.windll.kernel32
    done = []
    for name in names:
        for fn, callee, repl, count in KNOWN[name]['host']:
            sites = _calls_to(g, fn, callee)
            if len(sites) != count:
                raise RuntimeError(f'{name}: {len(sites)} calls to {callee} in {fn} (expected {count})')
            for off in sites:
                a = g.base + off
                if not hasattr(g, '_code_orig'):
                    g._code_orig = {}
                g._code_orig.setdefault(off, ctypes.string_at(a, len(repl)))
                _poke(a, repl)
                done.append((name, hex(off)))
    if done:
        k32.FlushInstructionCache(k32.GetCurrentProcess(), None, 0)
    g.code_patches = getattr(g, 'code_patches', []) + done
    return done


def _poke(a, data):
    k32 = ctypes.windll.kernel32
    old = ctypes.c_uint32()
    k32.VirtualProtect(ctypes.c_void_p(a), len(data), 0x40, ctypes.byref(old))
    ctypes.memmove(a, data, len(data))
    k32.VirtualProtect(ctypes.c_void_p(a), len(data), old.value, ctypes.byref(old))


def revert(g):
    for off, data in getattr(g, '_code_orig', {}).items():
        _poke(g.base + off, data)
    g._code_orig, g.code_patches = {}, []
    g.frame_hooks = [h for h in getattr(g, 'frame_hooks', []) if not isinstance(h, ObjectEdits)]
    ctypes.windll.kernel32.FlushInstructionCache(ctypes.windll.kernel32.GetCurrentProcess(), None, 0)
