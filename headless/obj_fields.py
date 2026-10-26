import ctypes
import glob
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import n64mem as NM
from n64bus import _obj_offsets

import hl_paths
DECOMP = hl_paths.DECOMP
FIELDS_H = os.path.join(DECOMP, 'include', 'object_fields.h')
BEHAVIOR_DATA = os.path.join(DECOMP, 'data', 'behavior_data.c')
SOURCES = glob.glob(os.path.join(DECOMP, 'src', 'game', 'behaviors', '*.inc.c')) + \
    glob.glob(os.path.join(DECOMP, 'src', 'game', '*.c'))
N_WORDS = 0x50
OBJ_POOL_N64, OBJ_STRIDE_N64, RAW_OFF_N64 = 0x8033D488, 0x260, 0x88
KIND_32, KIND_S16, KIND_PTR = '32', 's16', 'ptr'
FIELD_RE = re.compile(r'\bo[A-Z]\w*\b')
FUNC_DEF_RE = re.compile(r'^(?:static\s+)?(?:void|s32|s16|s8|u32|u16|u8|f32|Gfx\s*\*|struct\s+\w+\s*\*)\s+(\w+)\s*\([^;{]*\)\s*\{',
                         re.M)


def field_table():
    table = {}
    for m in re.finditer(r'#define\s+(?:/\*[^*]*\*/\s*)?(o\w+)\s+OBJECT_FIELD_(\w+)\((0x[0-9A-Fa-f]+|\d+)', open(FIELDS_H).read()):
        name, macro, idx = m.group(1), m.group(2), int(m.group(3), 0)
        if macro == 'S16':
            table[name] = (idx, KIND_S16)
        elif macro in ('S32', 'F32', 'U32'):
            table[name] = (idx, KIND_32)
        else:
            table[name] = (idx, KIND_PTR)
    return table


def _bodies():
    bodies = {}
    for path in SOURCES:
        txt = open(path, encoding='utf-8', errors='replace').read()
        for m in FUNC_DEF_RE.finditer(txt):
            start, depth, i = m.end() - 1, 0, m.end() - 1
            while i < len(txt):
                if txt[i] == '{':
                    depth += 1
                elif txt[i] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            bodies.setdefault(m.group(1), txt[start:i + 1])
    return bodies


def behavior_kinds(verbose=False):
    table, bodies = field_table(), _bodies()
    call_re = re.compile(r'\b(\w+)\s*\(')
    out, conflicts = {}, {}
    txt = open(BEHAVIOR_DATA, encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'const BehaviorScript (\w+)\[\] = \{(.*?)\};', txt, re.S):
        bhv, script = m.group(1), m.group(2)
        fields = set(FIELD_RE.findall(script))
        todo, seen = list(re.findall(r'CALL_NATIVE\((\w+)\)', script)), set()
        while todo:
            f = todo.pop()
            if f in seen or f not in bodies:
                continue
            seen.add(f)
            body = bodies[f]
            fields.update(FIELD_RE.findall(body))
            todo.extend(c for c in call_re.findall(body) if c in bodies and c not in seen)
        kinds = {}
        for name in fields:
            if name in table:
                idx, kind = table[name]
                kinds.setdefault(idx, set()).add(kind)
        out[bhv] = {i: next(iter(k)) for i, k in kinds.items() if len(k) == 1}
        conflicts[bhv] = {i: sorted(k) for i, k in kinds.items() if len(k) > 1}
        if verbose and conflicts[bhv]:
            print('conflict', bhv, conflicts[bhv])
    return out


def default_kind(i):
    return KIND_S16 if (i in NM.OBJ_S16_COMMON or i in NM.OBJ_S16_SPECIFIC) else KIND_32


def host_word(n64_bytes, kind):
    b = n64_bytes
    return bytes([b[1], b[0], b[3], b[2]]) if kind == KIND_S16 else bytes(b[::-1])


def fix_pool(g, rd, objects, kinds=None, apply=True, names=None):
    kinds = kinds or behavior_kinds()
    _raw_n, raw_h, pd = _obj_offsets()
    stride_h = NM.layout(NM.find_type('Object'))[2]
    pool_h = g.base + NM.G['gObjectPool']['rva']
    names = names or {}
    changes = []
    for o in objects:
        slot, bhv = o['slot'], o['bhv']
        bk = kinds.get(bhv, {})
        obj_h = pool_h + slot * stride_h
        raw_n = OBJ_POOL_N64 - 0x80000000 + slot * OBJ_STRIDE_N64 + RAW_OFF_N64
        for i, kind in bk.items():
            if kind == default_kind(i):
                continue
            if any(ctypes.string_at(obj_h + pd + 8 * i, 8)):
                continue
            want = host_word(rd[raw_n + 4 * i:raw_n + 4 * i + 4], kind)
            have = ctypes.string_at(obj_h + raw_h + 4 * i, 4)
            if want != have:
                changes.append({'slot': slot, 'bhv': bhv, 'index': hex(i), 'kind': kind, 'field': names.get((bhv, i)),
                                'before': have[::-1].hex(), 'after': want[::-1].hex()})
                if apply:
                    ctypes.memmove(obj_h + raw_h + 4 * i, want, 4)
    return changes


def check_layout(rd, objects, slot=105, expect_x=-2628.7197):
    raw_n = OBJ_POOL_N64 - 0x80000000 + slot * OBJ_STRIDE_N64 + RAW_OFF_N64
    x = struct.unpack_from('>f', rd, raw_n + 4 * 0x06)[0]
    if abs(x - expect_x) > 0.01:
        raise RuntimeError(f'N64 object pool layout mismatch: slot {slot} oPosX {x} (expected {expect_x})')
    return x
