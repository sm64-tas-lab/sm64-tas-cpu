import json, os, struct
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = os.environ.get('SM64_VERSION', 'us')
with open(os.path.join(HERE, '_types_cache.json' if VERSION == 'us' else f'_types_cache_{VERSION}.json'), encoding='utf-8') as _f:
    _D = json.load(_f)
T = _D['types']
G = _D['globals']


OBJ_S16_COMMON = {0x02, 0x4C}
OBJ_S16_SPECIFIC = {0x1B, 0x1F, 0x20, 0x21, 0x22, 0x49, 0x4A}

PTRINT = {'uintptr_t', 'intptr_t', 'size_t', 'ssize_t', 'ptrdiff_t', 'uintptr', 'u_intptr_t'}
PTRINT_T = '__ptrint__'


def strip(t):
    seen = 0
    while t is not None and T.get(t, {}).get('k') == 'alias':
        if T[t].get('n') in PTRINT:
            return PTRINT_T
        t = T[t]['t']
        seen += 1
        if seen > 50:
            break
    return t


def is_n64_absent(struct_name, member_name):
    return struct_name == 'Object' and member_name == 'ptrData'


@lru_cache(maxsize=None)
def layout(t):
    t = strip(t)
    if t is None:
        return (0, 1, 0, None)
    if t == PTRINT_T:
        return (4, 4, 8, None)
    d = T[t]
    k = d['k']
    if k == 'base':
        sz = d['size'] or 0
        return (sz, max(1, sz), sz, None)
    if k in ('ptr',):
        return (4, 4, 8, None)
    if k == 'func' or k == 'opaque':
        return (0, 1, 0, None)
    if k == 'array':
        n = 1
        for x in d['dims']:
            n *= x
        es, ea, eh, _ = layout(d['t'])
        return (es * n, ea, eh * n, 'array')
    if k in ('struct', 'union'):
        off = 0
        al = 1
        mx = 0
        for m in d['m']:
            if is_n64_absent(d.get('n'), m['n']):
                continue
            ms, ma, mh, _ = layout(m['t'])
            al = max(al, ma)
            if k == 'union':
                mx = max(mx, ms)
            else:
                off = (off + ma - 1) // ma * ma
                off += ms
        size = mx if k == 'union' else off
        size = (size + al - 1) // al * al
        return (size, al, d['size'], 'struct')
    return (0, 1, 0, None)


@lru_cache(maxsize=None)
def members(t):
    t = strip(t)
    d = T[t]
    out = []
    off = 0
    for m in d['m']:
        if is_n64_absent(d.get('n'), m['n']):
            continue
        ms, ma, mh, _ = layout(m['t'])
        if d['k'] == 'union':
            out.append((0, m['off'], m['t'], m['n']))
        else:
            off = (off + ma - 1) // ma * ma
            out.append((off, m['off'], m['t'], m['n']))
            off += ms
    return out


def resolve_prim(t, n64off):
    host = 0
    base = 0
    path = []
    while True:
        t = strip(t)
        if t is None:
            return None
        if t == PTRINT_T:
            return (t, base, host, '.'.join(path))
        d = T[t]
        k = d['k']
        if k in ('base', 'ptr'):
            return (t, base, host, '.'.join(path))
        if k == 'array':
            es, ea, eh, _ = layout(d['t'])
            if es == 0:
                return None
            n = 1
            for x in d['dims'][1:]:
                n *= x
            flat = 1
            for x in d['dims']:
                flat *= x
            i = (n64off - base) // es
            if i >= flat and flat:
                return None
            base += i * es
            host += i * eh
            path.append(f'[{i}]')
            t = d['t']
            continue
        if k in ('struct', 'union'):
            hit = None
            for mo, mh, mt, mn in members(t):
                ms = layout(mt)[0]
                if base + mo <= n64off < base + mo + ms:
                    hit = (mo, mh, mt, mn)
                    break
            if hit is None:
                return None
            base += hit[0]
            host += hit[1]
            path.append(hit[3] or '?')
            t = hit[2]
            continue
        return None


def host_to_n64(t, hoff):
    n = 0
    h = 0
    while True:
        t = strip(t)
        if t is None:
            return None
        if t == PTRINT_T:
            return n + (hoff - h) if hoff - h < 4 else None
        d = T[t]
        k = d['k']
        if k == 'base':
            return n + (hoff - h)
        if k == 'ptr':
            return n + (hoff - h) if hoff - h < 4 else None
        if k == 'array':
            es, ea, eh, _ = layout(d['t'])
            if eh == 0:
                return None
            i = (hoff - h) // eh
            n += i * es
            h += i * eh
            t = d['t']
            continue
        if k in ('struct', 'union'):
            hit = None
            for m in d['m']:
                mh = layout(m['t'])[2]
                if h + m['off'] <= hoff < h + m['off'] + max(mh, 1):
                    hit = m
                    break
            if hit is None or is_n64_absent(d.get('n'), hit['n']):
                return None
            mo = [x for x in members(t) if x[3] == hit['n']][0][0]
            n += mo
            h += hit['off']
            t = hit['t']
            continue
        return None


def type_of_global(name):
    return G[name]['type'] if name in G else None


def find_type(name):
    for k, v in T.items():
        if v['k'] in ('struct', 'union') and v.get('n') == name and v['m']:
            return k
    return None


if __name__ == '__main__':
    for n in ('MarioState', 'Object', 'Camera', 'Area', 'Surface', 'ObjectNode', 'GraphNodeObject', 'Controller'):
        t = find_type(n)
        s = layout(t)
        print(f'{n:16} N64 size=0x{s[0]:X} host size=0x{s[2]:X}')
