import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import n64mem as NM

import hl_paths
DECOMP = hl_paths.DECOMP
MAPF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_n64map.json')
DEFINES = {'VERSION_US', 'TARGET_N64', 'F3D_OLD', 'NON_MATCHING_ASM'}
DEFINES.discard('NON_MATCHING_ASM')
SRC_DIRS = ('src/game', 'src/engine')


def _eval_cond(expr):
    e = re.sub(r'defined\s*\(\s*(\w+)\s*\)', lambda m: str(m.group(1) in DEFINES), expr)
    e = re.sub(r'defined\s+(\w+)', lambda m: str(m.group(1) in DEFINES), e)
    e = e.replace('||', ' or ').replace('&&', ' and ').replace('!', ' not ')
    e = re.sub(r'\b(?!True\b|False\b|or\b|and\b|not\b)[A-Za-z_]\w*\b', 'False', e)
    try:
        return bool(eval(e, {}, {}))
    except Exception:
        return False


def preprocess(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'//[^\n]*', '', text)
    out, stack = [], []
    active = True
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('#'):
            d = s[1:].strip()
            if d.startswith('ifdef'):
                c = d.split()[1] in DEFINES
            elif d.startswith('ifndef'):
                c = d.split()[1] not in DEFINES
            elif d.startswith('if'):
                c = _eval_cond(d[2:])
            else:
                c = None
            if c is not None:
                stack.append((active, active and c, c))
                active = active and c
            elif d.startswith('elif') and stack:
                pa, _, taken = stack[-1]
                c = (not taken) and _eval_cond(d[4:])
                stack[-1] = (pa, pa and c, taken or c)
                active = pa and c
            elif d.startswith('else') and stack:
                pa, _, taken = stack[-1]
                stack[-1] = (pa, pa and not taken, True)
                active = pa and not taken
            elif d.startswith('endif') and stack:
                active = stack.pop()[0]
            continue
        if active:
            out.append(line)
    return '\n'.join(out)


def file_decls(path):
    text = preprocess(open(path, encoding='utf-8', errors='replace').read())
    decls, stmt, depth = [], '', 0
    for ch in text:
        if depth == 0:
            if ch == ';':
                decls.append(stmt.strip())
                stmt = ''
                continue
            if ch == '{':
                if '=' in stmt:
                    stmt += ' {INIT} '
                else:
                    stmt = ''
                depth = 1
                continue
            stmt += ch
        else:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
    order, defined, inited = [], set(), set()
    for s in decls:
        s = ' '.join(s.split())
        if not s or s.startswith('typedef') or 'const ' in s.split('=')[0]:
            continue
        ext = s.startswith('extern')
        lhs, init = (s.split('=', 1) + [None])[:2]
        if '(' in lhs and not re.search(r'\(\s*\*\s*\w+\s*\)', lhs):
            continue
        for part in lhs.split(','):
            m = re.search(r'(\w+)\s*(\[[^\]]*\]\s*)*\)?\s*(\[[^\]]*\]\s*)*$', part.strip())
            if not m:
                continue
            n = m.group(1)
            if n not in order:
                order.append(n)
            if not ext:
                defined.add(n)
                if init is not None:
                    inited.add(n)
    return [(n, n in inited) for n in order if n in defined]


def derive(files=None):
    known = json.load(open(MAPF))['globals']
    files = files or sorted(f for d in SRC_DIRS for f in glob.glob(os.path.join(DECOMP, d, '*.c')))
    derived, report = {}, {}
    for f in files:
        for want_init in (True, False):
            seq = [(n, i) for n, i in file_decls(f) if i == want_init and n in NM.G and NM.G[n]['type']]
            if not seq:
                continue
            addr, anchored, runs = None, False, []
            pending = []
            for n, _ in seq:
                size, align = NM.layout(NM.G[n]['type'])[:2]
                align = max(align, 1)
                pred = None if addr is None else (addr + align - 1) // align * align
                if n in known:
                    ok = pred is not None and pred == known[n]
                    if pending and anchored and ok:
                        derived.update(pending)
                        runs.append([p[0] for p in pending])
                    elif pending:
                        report.setdefault(os.path.basename(f), []).append(
                            ('rejected', [p[0] for p in pending], n, hex(known[n]), pred and hex(pred)))
                    pending, anchored, addr = [], True, known[n] + size
                else:
                    if pred is not None:
                        pending.append((n, pred))
                        addr = pred + size
            if pending:
                report.setdefault(os.path.basename(f), []).append(('trailing, unanchored', [p[0] for p in pending]))
            if runs:
                report.setdefault(os.path.basename(f), []).append(('ok', runs))
    return derived, report


def _ido_layout(n, data=False):
    size, align = NM.layout(NM.G[n]['type'])[:2]
    return size, max(align, 8 if size >= 8 else 1, 4 if data else 1)


def derive2(files=None):
    known = json.load(open(MAPF))['globals']
    files = files or sorted(f for d in SRC_DIRS for f in glob.glob(os.path.join(DECOMP, d, '*.c')))
    sure, amb = {}, {}
    for f in files:
        for want_init in (True, False):
            seq = [n for n, i in file_decls(f) if i == want_init and n in NM.G and NM.G[n]['type']]
            lay = {n: _ido_layout(n, want_init) for n in seq}
            idx = [i for i, n in enumerate(seq) if n in known]
            for a, b in zip(idx, idx[1:]):
                run = seq[a + 1:b]
                if not run:
                    continue
                fwd, addr = {}, known[seq[a]] + lay[seq[a]][0]
                for n in run:
                    al = max(lay[n][1], 1)
                    addr = (addr + al - 1) // al * al
                    fwd[n] = addr
                    addr += lay[n][0]
                bwd, addr = {}, known[seq[b]]
                for n in reversed(run):
                    al = max(lay[n][1], 1)
                    addr = (addr - lay[n][0]) // al * al
                    bwd[n] = addr
                for n in run:
                    if fwd[n] == bwd[n]:
                        sure[n] = fwd[n]
                    else:
                        amb[n] = (fwd[n], bwd[n])
    return sure, amb


if __name__ == '__main__':
    d, rep = derive()
    for f, items in rep.items():
        for it in items:
            print(f, str(it)[:300])
    print(len(d), 'derived')
    for n in ('gCamera', 'sStatusFlags', 'gCutsceneTimer', 'sCutsceneShot', 'gDialogID', 'gDialogBoxState',
              'gTimeStopState', 'gCameraMovementFlags', 'sModeInfo', 'gMenuMode', 'sCutsceneDialogID'):
        print(n, hex(d[n]) if n in d else None)
