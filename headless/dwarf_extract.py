import io, json, os, struct, sys
from elftools.dwarf.dwarfinfo import DWARFInfo, DebugSectionDescriptor, DwarfConfig

HERE = os.path.dirname(os.path.abspath(__file__))
VER = os.environ.get('SM64_VERSION', 'us')
DLL = os.path.join(os.path.dirname(HERE), f'sm64_{VER}.dll')
OUT = os.path.join(HERE, '_types_cache.json' if VER == 'us' else f'_types_cache_{VER}.json')


def pe_sections(f):
    pe = struct.unpack_from('<I', f, 0x3c)[0]
    ns = struct.unpack_from('<H', f, pe + 6)[0]
    osz = struct.unpack_from('<H', f, pe + 20)[0]
    symptr, nsym = struct.unpack_from('<II', f, pe + 12)
    strtab = symptr + nsym * 18
    imagebase = struct.unpack_from('<Q', f, pe + 24 + 24)[0]
    sec = pe + 24 + osz
    out = {}
    for i in range(ns):
        n = f[sec + i * 40:sec + i * 40 + 8].rstrip(b'\0').decode()
        if n.startswith('/'):
            o = strtab + int(n[1:])
            n = f[o:f.index(b'\0', o)].decode()
        vs, va, rs, rp = struct.unpack_from('<IIII', f, sec + i * 40 + 8)
        out[n] = (va, vs, rp, rs)
    return out, imagebase


def main():
    f = open(DLL, 'rb').read()
    secs, imagebase = pe_sections(f)

    def desc(name):
        if name not in secs:
            return None
        va, vs, rp, rs = secs[name]
        data = f[rp:rp + min(vs, rs) if vs else rs]
        return DebugSectionDescriptor(io.BytesIO(data), name, None, len(data), 0)

    di = DWARFInfo(config=DwarfConfig(little_endian=True, machine_arch='x64', default_address_size=8),
                   debug_info_sec=desc('.debug_info'), debug_aranges_sec=None, debug_abbrev_sec=desc('.debug_abbrev'),
                   debug_frame_sec=None, eh_frame_sec=None, debug_str_sec=desc('.debug_str'),
                   debug_loc_sec=None, debug_ranges_sec=None, debug_line_sec=None, debug_pubtypes_sec=None,
                   debug_pubnames_sec=None, debug_addr_sec=None, debug_str_offsets_sec=None,
                   debug_line_str_sec=desc('.debug_line_str'), debug_loclists_sec=None, debug_rnglists_sec=None,
                   debug_sup_sec=None, gnu_debugaltlink_sec=None, debug_types_sec=None)

    types = {}
    globals_ = {}
    tid = lambda die: f'{die.offset}'

    def attr(die, a, default=None):
        v = die.attributes.get(a)
        return v.value if v is not None else default

    def tref(die):
        v = die.attributes.get('DW_AT_type')
        if v is None:
            return None
        return str(v.value + (die.cu.cu_offset if v.form in ('DW_FORM_ref4', 'DW_FORM_ref2', 'DW_FORM_ref1', 'DW_FORM_ref8', 'DW_FORM_ref_udata') else 0))

    def nm(die):
        v = attr(die, 'DW_AT_name')
        return v.decode(errors='replace') if isinstance(v, bytes) else v

    ncu = 0
    for cu in di.iter_CUs():
        ncu += 1
        for die in cu.iter_DIEs():
            t = die.tag
            if t == 'DW_TAG_base_type':
                enc = attr(die, 'DW_AT_encoding')
                types[tid(die)] = {'k': 'base', 'n': nm(die), 'size': attr(die, 'DW_AT_byte_size'), 'float': enc == 4,
                                   'signed': enc in (5, 6)}
            elif t in ('DW_TAG_pointer_type', 'DW_TAG_reference_type'):
                types[tid(die)] = {'k': 'ptr', 't': tref(die)}
            elif t in ('DW_TAG_typedef', 'DW_TAG_const_type', 'DW_TAG_volatile_type', 'DW_TAG_restrict_type'):
                types[tid(die)] = {'k': 'alias', 't': tref(die), 'n': nm(die)}
            elif t == 'DW_TAG_enumeration_type':
                types[tid(die)] = {'k': 'base', 'n': nm(die), 'size': attr(die, 'DW_AT_byte_size', 4), 'float': False, 'signed': False}
            elif t == 'DW_TAG_subroutine_type':
                types[tid(die)] = {'k': 'func'}
            elif t in ('DW_TAG_structure_type', 'DW_TAG_union_type'):
                if attr(die, 'DW_AT_declaration'):
                    types[tid(die)] = {'k': 'decl', 'n': nm(die), 'union': t == 'DW_TAG_union_type'}
                    continue
                mem = []
                for c in die.iter_children():
                    if c.tag != 'DW_TAG_member':
                        continue
                    loc = attr(c, 'DW_AT_data_member_location', 0)
                    m = {'n': nm(c), 't': tref(c), 'off': loc if isinstance(loc, int) else 0}
                    if 'DW_AT_bit_size' in c.attributes or 'DW_AT_data_bit_offset' in c.attributes:
                        m['bits'] = True
                    mem.append(m)
                types[tid(die)] = {'k': 'union' if t == 'DW_TAG_union_type' else 'struct', 'n': nm(die),
                                   'size': attr(die, 'DW_AT_byte_size', 0), 'm': mem}
            elif t == 'DW_TAG_array_type':
                dims = []
                for c in die.iter_children():
                    if c.tag == 'DW_TAG_subrange_type':
                        ub = attr(c, 'DW_AT_upper_bound')
                        cnt = attr(c, 'DW_AT_count')
                        dims.append(cnt if cnt is not None else (ub + 1 if isinstance(ub, int) else 0))
                types[tid(die)] = {'k': 'array', 't': tref(die), 'dims': dims or [0]}
            elif t == 'DW_TAG_variable' and die.get_parent() is not None and die.get_parent().tag == 'DW_TAG_compile_unit':
                loc = die.attributes.get('DW_AT_location')
                if loc is None or not isinstance(loc.value, list) or not loc.value or loc.value[0] != 3:
                    continue
                addr = int.from_bytes(bytes(loc.value[1:9]), 'little')
                name, ty = nm(die), tref(die)
                spec = die.attributes.get('DW_AT_specification')
                if spec is not None and (name is None or ty is None):
                    sd = die.get_DIE_from_attribute('DW_AT_specification')
                    name = name or nm(sd)
                    ty = ty or tref(sd)
                if name is None:
                    continue
                globals_[name] = {'rva': addr - imagebase, 'type': ty}
        if ncu % 50 == 0:
            print(ncu, 'CU', len(types), 'types', len(globals_), 'globals', file=sys.stderr)
    defs = {}
    for k, v in types.items():
        if v['k'] in ('struct', 'union') and v.get('n'):
            defs.setdefault((v['k'], v['n']), k)
    for k, v in types.items():
        if v['k'] == 'decl':
            d = defs.get(('union' if v['union'] else 'struct', v['n']))
            types[k] = {'k': 'alias', 't': d, 'n': v['n']} if d else {'k': 'opaque', 'n': v['n']}
    json.dump({'types': types, 'globals': globals_}, open(OUT, 'w'))
    print('OK', len(types), 'types', len(globals_), 'globals ->', OUT)


if __name__ == '__main__':
    main()
