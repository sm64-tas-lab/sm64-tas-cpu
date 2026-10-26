import io, json, os, sys
from elftools.dwarf.dwarfinfo import DWARFInfo, DebugSectionDescriptor, DwarfConfig
import dwarf_extract as DX

HERE = os.path.dirname(os.path.abspath(__file__))
VER = os.environ.get('SM64_VERSION', 'us')
OUT = os.path.join(HERE, '_vars_cache.json' if VER == 'us' else f'_vars_cache_{VER}.json')


def main():
    f = open(DX.DLL, 'rb').read()
    secs, imagebase = DX.pe_sections(f)

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

    def attr(die, a, default=None):
        v = die.attributes.get(a)
        return v.value if v is not None else default

    def s(v):
        return v.decode(errors='replace') if isinstance(v, bytes) else v

    def tref(die):
        v = die.attributes.get('DW_AT_type')
        if v is None:
            return None
        return str(v.value + (die.cu.cu_offset if v.form in ('DW_FORM_ref4', 'DW_FORM_ref2', 'DW_FORM_ref1', 'DW_FORM_ref8', 'DW_FORM_ref_udata') else 0))

    out, funcs = [], []
    for cu in di.iter_CUs():
        top = cu.get_top_DIE()
        cuname = s(attr(top, 'DW_AT_name'))
        for die in cu.iter_DIEs():
            if die.tag == 'DW_TAG_subprogram':
                lo = attr(die, 'DW_AT_low_pc')
                if lo and attr(die, 'DW_AT_name'):
                    funcs.append({'n': s(attr(die, 'DW_AT_name')), 'rva': lo - imagebase, 'cu': cuname,
                                  'line': attr(die, 'DW_AT_decl_line')})
                continue
            if die.tag != 'DW_TAG_variable':
                continue
            loc = die.attributes.get('DW_AT_location')
            if loc is None or not isinstance(loc.value, list) or not loc.value or loc.value[0] != 3:
                continue
            addr = int.from_bytes(bytes(loc.value[1:9]), 'little')
            name, ty, line = s(attr(die, 'DW_AT_name')), tref(die), attr(die, 'DW_AT_decl_line')
            ext = bool(attr(die, 'DW_AT_external'))
            if die.attributes.get('DW_AT_specification') is not None:
                sd = die.get_DIE_from_attribute('DW_AT_specification')
                name = name or s(attr(sd, 'DW_AT_name'))
                ty = ty or tref(sd)
                line = line or attr(sd, 'DW_AT_decl_line')
                ext = ext or bool(attr(sd, 'DW_AT_external'))
            par = die.get_parent()
            fn = s(attr(par, 'DW_AT_name')) if par is not None and par.tag == 'DW_TAG_subprogram' else None
            if name is None:
                continue
            out.append({'n': name, 'rva': addr - imagebase, 'type': ty, 'cu': cuname, 'line': line, 'ext': ext, 'fn': fn})
    json.dump({'vars': out, 'funcs': funcs}, open(OUT, 'w'))
    print(len(out), 'variables,', len(funcs), 'fonctions ->', OUT)


if __name__ == '__main__':
    main()
