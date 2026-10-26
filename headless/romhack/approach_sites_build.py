import io, json, os, re, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import dwarf_extract as DE
from elftools.dwarf.dwarfinfo import DWARFInfo, DebugSectionDescriptor, DwarfConfig
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_REG, X86_OP_MEM, X86_REG_RIP, X86_REG_RSP

LLVM_MC = sys.argv[1] if len(sys.argv) > 1 else r'D:\LLVM\clang+llvm-23.1.1-x86_64-pc-windows-msvc\bin\llvm-mc.exe'
OUT = os.path.join(HERE, '_approach_sites.json')
FN = 'approach_f32_asymptotic_bool'

f = open(DE.DLL, 'rb').read()
secs, ib = DE.pe_sections(f)


def rd(rva, n):
    for nm, (a, s_, p, r) in secs.items():
        if a <= rva < a + max(s_, r):
            return f[p + rva - a:p + rva - a + n]
    return b''


def desc(name):
    if name not in secs:
        return None
    va, vs, rp, rs = secs[name]
    data = f[rp:rp + min(vs, rs)]
    return DebugSectionDescriptor(io.BytesIO(data), name, None, len(data), 0)


di = DWARFInfo(config=DwarfConfig(little_endian=True, machine_arch='x64', default_address_size=8),
               debug_info_sec=desc('.debug_info'), debug_aranges_sec=None, debug_abbrev_sec=desc('.debug_abbrev'),
               debug_frame_sec=None, eh_frame_sec=None, debug_str_sec=desc('.debug_str'), debug_loc_sec=None,
               debug_ranges_sec=desc('.debug_ranges'), debug_line_sec=None, debug_pubtypes_sec=None,
               debug_pubnames_sec=None, debug_addr_sec=None, debug_str_offsets_sec=None,
               debug_line_str_sec=desc('.debug_line_str'), debug_loclists_sec=None, debug_rnglists_sec=None,
               debug_sup_sec=None, gnu_debugaltlink_sec=None, debug_types_sec=None)
copies = []
for cu in di.iter_CUs():
    top = cu.get_top_DIE()
    if not top.attributes.get('DW_AT_name') or b'camera.c' not in top.attributes['DW_AT_name'].value:
        continue
    base = top.attributes['DW_AT_low_pc'].value if 'DW_AT_low_pc' in top.attributes else 0
    for die in cu.iter_DIEs():
        if die.tag != 'DW_TAG_inlined_subroutine' or 'DW_AT_abstract_origin' not in die.attributes:
            continue
        org = die.get_DIE_from_attribute('DW_AT_abstract_origin')
        if org.attributes.get('DW_AT_name') is None or org.attributes['DW_AT_name'].value.decode() != FN:
            continue
        par = die.get_parent()
        while par is not None and par.tag != 'DW_TAG_subprogram':
            par = par.get_parent()
        pn = par.attributes.get('DW_AT_name') if par is not None else None
        if pn is None and par is not None and 'DW_AT_abstract_origin' in par.attributes:
            pn = par.get_DIE_from_attribute('DW_AT_abstract_origin').attributes.get('DW_AT_name')
        rng = []
        if 'DW_AT_low_pc' in die.attributes:
            lo, hi = die.attributes['DW_AT_low_pc'].value, die.attributes['DW_AT_high_pc']
            rng.append((lo - ib, lo - ib + (hi.value if hi.form != 'DW_FORM_addr' else hi.value - lo)))
        else:
            for e in di.range_lists().get_range_list_at_offset(die.attributes['DW_AT_ranges'].value, cu=cu):
                if hasattr(e, 'begin_offset'):
                    b = e.begin_offset + (0 if getattr(e, 'is_absolute', False) else base)
                    rng.append((b - ib, b - ib + (e.end_offset - e.begin_offset)))
        copies.append({'in': pn.value.decode() if pn else '?', 'ranges': sorted(rng)})
print(len(copies), 'copies of', FN)

md = Cs(CS_ARCH_X86, CS_MODE_64)
md.detail = True
lo_all = min(r[0] for c in copies for r in c['ranges']) - 0x4000
hi_all = max(r[1] for c in copies for r in c['ranges']) + 0x4000
ins_at, order = {}, []
for ins in md.disasm(rd(lo_all, hi_all - lo_all), lo_all):
    ins_at[ins.address] = ins
    order.append(ins.address)
idx = {a: i for i, a in enumerate(order)}
targets = set()
for a in order:
    ins = ins_at[a]
    if ins.mnemonic.startswith('j') or ins.mnemonic == 'call':
        op = ins.operands[0] if ins.operands else None
        if op is not None and op.type == 2:
            targets.add(op.imm)
rn = md.reg_name


def rip_target(ins, op):
    return ins.address + ins.size + op.mem.disp


def trace(i0, reg, lo, depth=0):
    i = i0 - 1
    while i >= 0 and order[i] >= lo and depth < 40:
        ins = ins_at[order[i]]
        ops = ins.operands
        if ops and ops[0].type == X86_OP_REG and rn(ops[0].reg) == reg:
            mn = ins.mnemonic
            if mn in ('movss', 'movaps', 'movups', 'movd', 'movq') and len(ops) == 2:
                s = ops[1]
                if s.type == X86_OP_MEM and s.mem.base == X86_REG_RIP:
                    return [('const', ins.address)]
                if s.type == X86_OP_REG:
                    return trace(i, rn(s.reg), lo, depth + 1)
                return [('mem', ins.address)]
            if mn in ('shufps', 'pshufd') and rn(ops[1].reg) == reg:
                return trace(i, reg, lo, depth + 1)
            if mn == 'minss':
                return [('min', ins.address)]
            if mn in ('unpcklps', 'movlhps') and ops[1].type == X86_OP_REG:
                return trace(i, reg, lo, depth + 1) + trace(i, rn(ops[1].reg), lo, depth + 1)
            return [('other', ins.address, mn)]
        i -= 1
    return [('none',)]


const_sites, min_sites, problems = {}, {}, []
for c in copies:
    lo = c['ranges'][0][0] - 0x200
    for a0, a1 in c['ranges']:
        a = a0
        while a < a1 and a in ins_at:
            ins = ins_at[a]
            if ins.mnemonic in ('mulss', 'mulps'):
                srcs = []
                for op in ins.operands:
                    if op.type == X86_OP_REG:
                        srcs.append(trace(idx[a], rn(op.reg), lo))
                    elif op.mem.base == X86_REG_RIP:
                        srcs.append([('const', a)])
                    else:
                        srcs.append([('mem', a)])
                mult = [s for s in srcs if not any(x[0] == 'other' and x[2] in ('subss', 'subps') for x in s)]
                if len(mult) == 2 and any(m_ == [('mem', m_[0][1])] for m_ in mult):
                    mult = [m_ for m_ in mult if m_[0][0] != 'mem']
                if len(mult) != 1:
                    problems.append((c['in'], hex(a), ins.mnemonic, ins.op_str, srcs))
                else:
                    for x in mult[0]:
                        if x[0] == 'const':
                            const_sites[x[1]] = c['in']
                        elif x[0] == 'min':
                            min_sites[x[1]] = c['in']
                        else:
                            problems.append((c['in'], hex(a), ins.mnemonic, ins.op_str, mult[0]))
            a += ins.size

consts = []
for a, fn in sorted(const_sites.items()):
    ins = ins_at[a]
    mem = [op for op in ins.operands if op.type == X86_OP_MEM][0]
    width = {'movss': 4, 'mulss': 4, 'movd': 4, 'movq': 8, 'movaps': 16, 'movups': 16, 'mulps': 16}[ins.mnemonic]
    code = rd(a, ins.size)
    tgt = rip_target(ins, mem)
    dpos = ins.size - 4
    assert struct.unpack_from('<i', code, dpos)[0] == mem.mem.disp, (hex(a), ins.mnemonic)
    consts.append({'at': a, 'fn': fn, 'insn': code.hex(), 'disp_at': dpos, 'width': width, 'const': tgt,
                   'values': list(struct.unpack('<%df' % (width // 4), rd(tgt, width)))})

TMPS = ['xmm13', 'xmm14', 'xmm15', 'xmm12', 'xmm11']


def mc(lines):
    with tempfile.NamedTemporaryFile('w', suffix='.s', delete=False) as t:
        t.write('\n'.join(lines) + '\n')
    r = subprocess.run([LLVM_MC, '-triple=x86_64-pc-windows-msvc', '-x86-asm-syntax=intel', '-show-encoding', t.name],
                       capture_output=True, text=True)
    os.unlink(t.name)
    if r.returncode:
        raise RuntimeError(r.stderr)
    out = []
    for line in r.stdout.splitlines():
        m = re.search(r'encoding: \[([^\]]*)\]', line)
        if m:
            out.append(bytes(int(x, 16) for x in m.group(1).split(',')))
    return out


def f_body(ins, temps):
    d, s = ins.operands
    xd = rn(d.reg)
    t1, t2, t3 = temps
    if s.type == X86_OP_REG:
        load_s = f'movaps {t1}, {rn(s.reg)}'
    else:
        if s.mem.base == X86_REG_RIP or s.mem.index:
            raise ValueError('minss operand ' + ins.op_str)
        disp = s.mem.disp + (48 if s.mem.base == X86_REG_RSP else 0)
        load_s = f'movss {t1}, dword ptr [{rn(s.mem.base)} + {disp}]'
    return [load_s,
            f'movss {t2}, dword ptr [rip + @ONE]', f'cmpeqss {t2}, {xd}', f'movaps {t3}, {t2}', f'andps {t2}, {t1}',
            f'andnps {t3}, {xd}', f'orps {t2}, {t3}',
            f'movss {t1}, dword ptr [rip + @T]', f'cmpltss {t1}, {t2}', f'movss {t3}, dword ptr [rip + @V]',
            f'andps {t3}, {t1}', f'andnps {t1}, {t2}', f'orps {t1}, {t3}', f'movss {xd}, {t1}']


mins, done = [], set()
for a, fn in sorted(min_sites.items()):
    if a in done:
        continue
    ins = ins_at[a]
    def movable(x):
        return x.address in min_sites or not (x.mnemonic.startswith('j') or x.mnemonic in ('call', 'ret')
                                               or 'rip' in x.op_str or 'rsp' in x.op_str)
    pre, post, n, start = [], [], ins.size, a
    while n < 5:
        nx = ins_at.get(a + ins.size + sum(c.size for c in post))
        if nx is None or not movable(nx) or nx.address in targets:
            break
        post.append(nx)
        n += nx.size
    while n < 5:
        pv = ins_at.get(order[idx[start] - 1])
        if pv is None or not movable(pv) or start in targets or pv.address in done:
            break
        pre.insert(0, pv)
        start = pv.address
        n += pv.size
    group = pre + [ins] + post
    if n < 5 or any((start + k) in targets for k in range(1, n)):
        problems.append((fn, hex(a), 'no room for a jmp', [c.mnemonic for c in group]))
        continue
    used = set(re.findall(r'xmm\d+', ' '.join(c.op_str for c in group)))
    temps = [t for t in TMPS if t not in used][:3]
    lines = ['lea rsp, [rsp - 48]', f'movups xmmword ptr [rsp], {temps[0]}',
             f'movups xmmword ptr [rsp + 16], {temps[1]}', f'movups xmmword ptr [rsp + 32], {temps[2]}']
    for c in group:
        if c.address in min_sites:
            lines += f_body(c, temps)
            done.add(c.address)
        else:
            lines.append('.byte ' + ', '.join(str(x) for x in c.bytes))
    lines += [f'movups {temps[0]}, xmmword ptr [rsp]', f'movups {temps[1]}, xmmword ptr [rsp + 16]',
              f'movups {temps[2]}, xmmword ptr [rsp + 32]', 'lea rsp, [rsp + 48]', 'jmp qword ptr [rip + @RET]']

    def size(l):
        if l.startswith('.byte'):
            return l.count(',') + 1
        return len(mc([re.sub(r'@[A-Z]+', '0', l)])[0])
    sizes = [size(l) for l in lines]
    code_len = sum(sizes)
    data = {'ONE': code_len, 'T': code_len + 4, 'V': code_len + 8, 'RET': code_len + 12}
    out, pos = b'', 0
    for l, z in zip(lines, sizes):
        if l.startswith('.byte'):
            out += bytes(int(x) for x in l[6:].split(','))
        else:
            for k, off in data.items():
                l = l.replace('@' + k + ']', str(off - (pos + z)) + ']')
            e = mc([l])[0]
            assert len(e) == z, l
            out += e
        pos += z
    assert len(out) == code_len
    mins.append({'at': start, 'fn': fn, 'insn': rd(start, n).hex(), 'len': n, 'resume': start + n, 'stub': out.hex(),
                 'data_at': data, 'minss': [f'{c.address:#x} {c.mnemonic} {c.op_str}' for c in group
                                            if c.address in min_sites]})

json.dump({'note': f'sm64_us.dll ({os.path.getsize(DE.DLL)} bytes): inlined {FN} multipliers (approach_sites_build.py)',
           'dll_size': os.path.getsize(DE.DLL), 'const': consts, 'min': mins},
          open(OUT, 'w'), indent=0)
print(f'{len(consts)} constant sites, {len(mins)} minss sites -> {OUT}')
for p in problems:
    print('  NOT HANDLED:', p)
