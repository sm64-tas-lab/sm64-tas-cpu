import ctypes
import json
import os
import shutil
import struct
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DLL4 = os.path.join(HERE, 'romhack', 'rh_collision4.dll')

BOUNDS4_SIG = {0x8038193C: 0x000F7883, 0x80381964: 0x00094883, 0x803806A8: 0x3C144080}
BOUNDS4_FUNCS = ('find_wall_collisions', 'f32_find_wall_collision', 'find_ceil', 'find_floor', 'find_floor_height',
                 'find_floor_height_and_data', 'unused_find_dynamic_floor', 'load_object_surfaces')


CAMTAB_CALL = (0x80286AD8, 0x0C100BD4)
CAMTAB_CODE = {0x80402F58: 0x3C018033, 0x80402F5C: 0x8421DDF8, 0x80402F64: 0x35082E50}
CAMTAB_TABLE, CAMTAB_LEN = 0x80402E50, 64


CAMW_SIG = {0x8028A614: 0x46144203, 0x8028FC10: 0x00000000}
DLLCAM = os.path.join(HERE, 'romhack', 'rh_camera4.dll')


CAMYAW_SIG = {0x80284330: 0x34060000, 0x8028432C: 0x0C0A25DB}


ISLAND_SIGS = {
    'island_2d': {0x80248374: 0x0C09DFD4, 0x8024837C: 0x8C495FFC, 0x8024839C: 0x90495F0F,
                  0x802815C0: 0x90495F0F, 0x802815C8: 0x15200016},
    'obj_no_12k': {0x802A25BC: 0x0C0A8934, 0x802A25C4: 0x00000000},
    'finish_line_2d': {0x80312168: 0x27BDFFF0, 0x80312178: 0x354A0010, 0x803121A4: 0xAD09B1AC, 0x803121C0: 0x0C0A21C6},
    'act_select_table': {0x8024BF38: 0x2B010001, 0x8024BF50: 0x08100BC0, 0x80402F00: 0x3C018033, 0x80402F04: 0x8421DDF8,
                         0x80402F08: 0x3C088040, 0x80402F10: 0x01014021, 0x80402F14: 0x91080000, 0x80402F18: 0x24010001,
                         0x80402F1C: 0x10280003, 0x80402F24: 0x08092FE4, 0x80402F2C: 0x08092FD6},
}
STAR_HELPER = {0x80402700: 0x27BDFFE8, 0x80402708: 0xC48C0000, 0x8040270C: 0xC48E0004, 0x80402710: 0x0C0BCAE2}
STAR_KING = (0x802A7AD4, 0x802A7AD8, 0x802A7ADC)
STAR_BULLY = (0x802EB970, 0x802EB974, 0x802EB978)
STAR_WHOMP = (0x802C7900, 0x802C7904, 0x802C7910)
WHOMP_SCALE = 0x802C6DAC
FOV_CONST = 0x8027D55C
APPROACH_SIG = {0x802893F4: 0xAFA50004, 0x802893F8: 0xAFA60008, 0x80289400: 0x44813000, 0x80289404: 0xC7A40008,
                0x80289408: 0x4604303C, 0x8028941C: 0x44814000, 0x80289424: 0xE7A80008}
APPROACH_T, APPROACH_V = 0x802893FC, 0x80289418
APPROACH_SITES = os.path.join(HERE, 'romhack', '_approach_sites.json')
DLLISL = os.path.join(HERE, 'romhack', 'rh_island.dll')


FLOORHI_SIG = {0x80381578: 0x3C08DFFF, 0x8038157C: 0xAFA80020, 0x80381728: 0xC7B80020, 0x80381738: 0x4618303C,
               0x8038176C: 0xE7A60020, 0x80381770: 0xE7060000, 0x80381774: 0x1480FF82}


CAM8_SIG = {0x80286BBC: 0x10000045, 0x80286C7C: 0x1000EFEF, 0x80282C3C: 0x3C028033, 0x80282C40: 0x3C038034,
            0x80282D38: 0xA464C778, 0x802870A8: 0x9508B19E, 0x802870AC: 0x21089000, 0x802870B0: 0x3108E000,
            0x802870B4: 0xA428C778}
CAM8_TOGGLE, CAM8_LAST = 0x80286D24, 0x80286D20
CAM8_STATE = (('unused8032CFC0', 's32'), ('unused8032D008', 'u32'))
CAM8_FUNCS = (('behind', 'mode_behind_mario_camera'), ('cup', 'mode_c_up_camera'),
              ('water', 'mode_water_surface_camera'), ('cannon', 'mode_cannon_camera'),
              ('eightdir', 'mode_8_directions_camera'), ('radial', 'mode_radial_camera'),
              ('outward', 'mode_outward_radial_camera'), ('lakitu', 'mode_lakitu_camera'),
              ('boss', 'mode_boss_fight_camera'), ('parallel', 'mode_parallel_tracking_camera'),
              ('slide', 'mode_slide_camera'), ('fixed', 'mode_fixed_camera'), ('spiral', 'mode_spiral_stairs_camera'),
              ('mario', 'mode_mario_camera'))
CAM8_RESET_CALLERS = ('init_mario_after_warp', 'warp_credits', 'init_level')
DLLCAM8 = os.path.join(HERE, 'romhack', 'rh_camera8.dll')


CSIDE_SIG = {0x8028BF14: 0x240E0001, 0x8028BF24: 0x85F80010, 0x8028BFA8: 0x87280010,
             0x8028BF88: 0x00000000, 0x8028C00C: 0x00000000}
CSIDE_HOST = {
    0x72: (bytes.fromhex('7449'), bytes.fromhex('7442')),
    0xB9: (bytes.fromhex('0FB74012'), bytes.fromhex('0FB74010')),
    0x176: (bytes.fromhex('0FB74012'), bytes.fromhex('0FB74010')),
    0x204: (bytes.fromhex('0FB74012'), bytes.fromhex('0FB74010')),
    0xE5: (bytes.fromhex('BA00F0FFFF'), bytes.fromhex('BAFFFFFFFF')),
    0x11C: (bytes.fromhex('B800100000'), bytes.fromhex('B801000000')),
    0x1FC: (None, bytes.fromhex('0F1F440000')),
    0x21C: (None, bytes.fromhex('0F1F440000')),
}


def detect(rd):
    get = lambda a: struct.unpack_from('>I', rd, a - 0x80000000)[0]
    out = ['bounds4'] if all(get(a) == w for a, w in BOUNDS4_SIG.items()) else []
    try:
        if get(CAMTAB_CALL[0]) == CAMTAB_CALL[1] and all(get(a) == w for a, w in CAMTAB_CODE.items()):
            out.append('camera_course_table')
        if all(get(a) == w for a, w in CAMW_SIG.items()):
            out.append('camera_walls4')
        if all(get(a) == w for a, w in CAMYAW_SIG.items()):
            out.append('camera_default_yaw0')
        if all(get(a) == w for a, w in CSIDE_SIG.items()):
            out.append('camera_cside_held')
        if all(get(a) == w for a, w in CAM8_SIG.items()):
            out.append('camera_8dir_all')
        if 'bounds4' in out and all(get(a) == w for a, w in FLOORHI_SIG.items()):
            out.append('floor_highest')
        out += [n for n, sig in ISLAND_SIGS.items() if all(get(a) == w for a, w in sig.items())]
        if all(get(a) == w for a, w in STAR_HELPER.items()) and any(
                get(s[1]) == 0x0C1009C0 for s in (STAR_KING, STAR_BULLY)) or (
                get(STAR_WHOMP[0]) >> 16 == 0x3C01 and get(STAR_WHOMP[0]) & 0xFFFF != 0x4334
                and get(STAR_WHOMP[2]) == 0x0C0BCAE2):
            out.append('boss_star_pos')
        if get(WHOMP_SCALE) >> 16 == 0x3C01 and get(WHOMP_SCALE) != 0x3C014000:
            out.append('whomp_scale')
        if get(FOV_CONST) >> 16 == 0x3C01 and get(FOV_CONST) != 0x3C014700:
            out.append('obj_fov')
        if (all(get(a) == w for a, w in APPROACH_SIG.items()) and get(APPROACH_T) >> 16 == 0x3C01
                and get(APPROACH_V) >> 16 == 0x3C01 and (get(APPROACH_T), get(APPROACH_V)) != (0x3C013F80, 0x3C013F80)):
            out.append('camera_approach')
        out += rm_detect(rd)
    except struct.error:
        pass
    return out


def _pid_alive(pid):
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_uint32()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259


_SWEPT = set()


def _dll_copy(src, tag, g):
    d = tempfile.gettempdir()
    if tag not in _SWEPT:
        _SWEPT.add(tag)
        for f in os.listdir(d):
            parts = f[:-4].split('_') if f.startswith(tag + '_') and f.endswith('.dll') else None
            if parts and len(parts) >= 3 and parts[-2].isdigit() and int(parts[-2]) != os.getpid() \
                    and not _pid_alive(int(parts[-2])):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    tmp = os.path.join(d, f'{tag}_{os.getpid()}_{id(g):x}.dll')
    shutil.copyfile(src, tmp)
    return tmp


def _apply_camwalls(g):
    import st_patches as SP
    sites = SP._calls_to(g, 'rotate_camera_around_walls', 'is_surf_within_bounding_box')
    if len(sites) != 1:
        raise RuntimeError(f'camera_walls4: {len(sites)} calls in rotate_camera_around_walls (expected 1)')
    tmp = _dll_copy(DLLCAM, 'rh_camera4', g)
    lib = ctypes.CDLL(tmp)
    saved = []
    site = g.base + sites[0]
    saved.append((site, ctypes.string_at(site, 5)))
    _write_code(site, bytes.fromhex('31C00F1F00'))
    entry = g.addr('is_behind_surface')
    stub = bytes.fromhex('48B8') + struct.pack('<Q', ctypes.cast(lib.rh_is_behind_surface, ctypes.c_void_p).value) +         bytes.fromhex('FFE0')
    saved.append((entry, ctypes.string_at(entry, len(stub))))
    _write_code(entry, stub)
    g._rh_camwalls = (saved, lib)
    return ['camera_walls4']


def _alloc_near(base, size):
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    for k in range(1, 4096):
        for hint in (base - k * 0x10000, base + 0x4000000 + k * 0x10000):
            if hint <= 0:
                continue
            p = k32.VirtualAlloc(ctypes.c_void_p(hint), size, 0x3000, 0x40)
            if p and abs(p - base) < 0x7FF00000:
                return p
    raise RuntimeError('no executable memory near the DLL')


def _apply_camtab(g, rd):
    import st_patches as SP
    sites = SP._calls_to(g, 'update_camera', 'camera_course_processing')
    if len(sites) != 1:
        raise RuntimeError(f'camera_course_table: {len(sites)} calls in update_camera (expected 1)')
    o = CAMTAB_TABLE - 0x80000000
    table = ctypes.create_string_buffer(bytes(rd[o:o + CAMTAB_LEN]), CAMTAB_LEN)
    ccp = g.addr('camera_course_processing')
    code = (bytes.fromhex('48B8') + struct.pack('<Q', g.addr('gCurrLevelNum')) +
            bytes.fromhex('480FBF00') +
            bytes.fromhex('48BA') + struct.pack('<Q', ctypes.addressof(table)) +
            bytes.fromhex('803C0201') +
            bytes.fromhex('750C') +
            bytes.fromhex('48B8') + struct.pack('<Q', ccp) + bytes.fromhex('FFE0') +
            bytes.fromhex('C3'))
    site = g.base + sites[0]
    tramp = _alloc_near(site, 0x1000)
    ctypes.memmove(tramp, code, len(code))
    orig = ctypes.string_at(site, 5)
    _write_code(site, bytes.fromhex('E8') + struct.pack('<i', tramp - (site + 5)))
    g._rh_camtab = (site, orig, table, tramp)
    return ['camera_course_table']


def _cam8_seed(g, rd):
    for (nm, ty), a in zip(CAM8_STATE, (CAM8_TOGGLE, CAM8_LAST)):
        ctypes.memmove(g.addr(nm), struct.pack('<I', _u32(rd, a)), 4)


def _apply_cam8(g, rd):
    import n64mem as NM
    import st_patches as SP
    tmp = _dll_copy(DLLCAM8, 'rh_camera8', g)
    lib = ctypes.CDLL(tmp)
    mem = lambda t: {m[3]: m[1] for m in NM.members(NM.find_type(t))}
    ctl, pcs, ms = mem('Controller'), mem('PlayerCameraState'), mem('MarioState')
    a = g.addr

    class Globals(ctypes.Structure):
        _fields_ = [('gPlayer1Controller', ctypes.c_void_p), ('ctlDownOff', ctypes.c_int64),
                    ('ctlPressedOff', ctypes.c_int64), ('s8DirModeYawOffset', ctypes.c_void_p),
                    ('sSelectionFlags', ctypes.c_void_p), ('sMarioCamState', ctypes.c_void_p),
                    ('camStateYawOff', ctypes.c_int64), ('gMarioStates', ctypes.c_void_p), ('msYawOff', ctypes.c_int64),
                    ('fn', ctypes.c_void_p * 15), ('toggle', ctypes.c_void_p), ('last', ctypes.c_void_p)]
    fns = [a(f) for _, f in CAM8_FUNCS] + [a('reset_camera')]
    gl = Globals(a('gPlayer1Controller'), ctl['buttonDown'], ctl['buttonPressed'], a('s8DirModeYawOffset'),
                 a('sSelectionFlags'), a('sMarioCamState'), pcs['faceAngle'] + 2, a('gMarioStates'),
                 ms['faceAngle'] + 2, (ctypes.c_void_p * 15)(*fns), a(CAM8_STATE[0][0]), a(CAM8_STATE[1][0]))
    lib.rh_cam8_init(ctypes.byref(gl))
    _cam8_seed(g, rd)
    saved, counts = [], {}
    for short, fn in CAM8_FUNCS:
        sites = SP._calls_to(g, 'update_camera', fn)
        counts[fn] = len(sites)
        target = ctypes.cast(getattr(lib, 'rh_cam8_' + short), ctypes.c_void_p).value
        for s in sites:
            saved.append(_redirect(g, s, target))
    reset = ctypes.cast(lib.rh_cam8_reset, ctypes.c_void_p).value
    for caller in CAM8_RESET_CALLERS:
        sites = SP._calls_to(g, caller, 'reset_camera')
        counts[caller + '>reset_camera'] = len(sites)
        for s in sites:
            saved.append(_redirect(g, s, reset))
    g._rh_cam8 = (saved, lib, gl, counts)
    return ['camera_8dir_all']


class _Globals(ctypes.Structure):
    _fields_ = [(n, ctypes.c_void_p) for n in (
        'staticPart', 'dynamicPart', 'checkingCam', 'includeIntangible', 'currentObject', 'marioObject', 'marioState',
        'surfacesAllocated', 'surfaceNodesAllocated', 'surfacePool', 'surfaceNodePool', 'numCalls',
        'numFindFloorMisses', 'floorGeo', 'bhvDDDWarp')] + [('oActiveFlags', ctypes.c_int32), ('oBehavior', ctypes.c_int32)]


def _write_code(addr, data):
    k32 = ctypes.windll.kernel32
    old = ctypes.c_uint32()
    k32.VirtualProtect(ctypes.c_void_p(addr), len(data), 0x40, ctypes.byref(old))
    ctypes.memmove(addr, data, len(data))
    k32.VirtualProtect(ctypes.c_void_p(addr), len(data), old.value, ctypes.byref(old))
    k32.FlushInstructionCache(k32.GetCurrentProcess(), ctypes.c_void_p(addr), len(data))


def _u32(rd, a):
    return struct.unpack_from('>I', rd, a - 0x80000000)[0]


def _f32(rd, a):
    return struct.unpack_from('>f', rd, a - 0x80000000)[0]


def _stub_page(g):
    pg = getattr(g, '_rh_stub_page', None)
    if pg is None:
        pg = g._rh_stub_page = [_alloc_near(g.base, 0x1000), 0]
    return pg


def _stub(g, code):
    pg = _stub_page(g)
    a = pg[0] + pg[1]
    if pg[1] + len(code) > 0x1000:
        raise RuntimeError('stub page full')
    ctypes.memmove(a, code, len(code))
    pg[1] += (len(code) + 15) & ~15
    return a


def _jmp_abs(target):
    return bytes.fromhex('48B8') + struct.pack('<Q', target) + bytes.fromhex('FFE0')


def _set_xmm(k, v):
    return bytes.fromhex('B8') + struct.pack('<f', v) + bytes([0x66, 0x0F, 0x6E, 0xC0 | (k << 3)])


def _redirect(g, site, target):
    a = g.base + site
    old = ctypes.string_at(a, 5)
    if old[0] != 0xE8:
        raise RuntimeError(f'not a call at +{site:#x} ({old.hex()})')
    st = _stub(g, _jmp_abs(target))
    _write_code(a, bytes.fromhex('E8') + struct.pack('<i', st - (a + 5)))
    return (a, old)


def _one_call(g, fn, callee, pick=None):
    import st_patches as SP
    sites = SP._calls_to(g, fn, callee)
    if pick:
        sites = [s for s in sites if pick(s)]
    if len(sites) != 1:
        raise RuntimeError(f'{fn}: {len(sites)} matching calls to {callee} (expected 1): this DLL build differs')
    return sites[0]


def _loads_before(g, site, n=48):
    a0 = g.base + site - n
    code = ctypes.string_at(a0, n)
    out = {}
    for i in range(n - 8):
        if code[i:i + 3] == b'\xF3\x0F\x10' and code[i + 3] & 0xC7 == 0x05:
            d = struct.unpack_from('<i', code, i + 4)[0]
            out[(code[i + 3] >> 3) & 7] = struct.unpack('<f', ctypes.string_at(a0 + i + 8 + d, 4))[0]
    return out


def _island_lib(g):
    lib = getattr(g, '_rh_island_lib', None)
    if lib is not None:
        return lib
    import n64mem as NM
    tmp = _dll_copy(DLLISL, 'rh_island', g)
    lib = ctypes.CDLL(tmp)
    ob = {m[3]: m[1] for m in NM.members(NM.find_type('Object'))}
    ms = {m[3]: m[1] for m in NM.members(NM.find_type('MarioState'))}
    a = g.addr
    try:
        hud = a('sCameraHUDStatus')
    except Exception:
        hud = 0

    class Globals(ctypes.Structure):
        _fields_ = [(n, ctypes.c_void_p) for n in ('gCurrentObject', 'gMarioStates')] + [('msPosOff', ctypes.c_int64)] + \
                   [(n, ctypes.c_void_p) for n in ('gControllers', 'gPlayer1Controller', 'sCameraHUD', 's8DirModeYawOffset',
                                                  'gCurrLevelNum', 'gCurrAreaIndex')] + [('objRawOff', ctypes.c_int64)] + \
                   [(n, ctypes.c_void_p) for n in ('set_cam_angle', 'radial_camera_input', 'adjust_analog_stick')]
    gl = Globals(a('gCurrentObject'), a('gMarioStates'), ms['pos'], a('gControllers'), a('gPlayer1Controller'), hud,
                 a('s8DirModeYawOffset'), a('gCurrLevelNum'), a('gCurrAreaIndex'), ob['rawData'],
                 a('set_cam_angle'), a('radial_camera_input'), a('adjust_analog_stick'))
    lib.rh_init(ctypes.byref(gl))
    g._rh_island_lib, g._rh_island_gl = lib, gl
    island_area_flags(g)
    return lib


def island_area_flags(g, flags=None):
    lib = getattr(g, '_rh_island_lib', None)
    if lib is None:
        return {}
    if flags is None:
        lv = getattr(g, '_rh_levels', None)
        flags = dict(getattr(lv, 'area_flags', {}) or {})
    tab = bytearray(64 * 16)
    for lvl, b in flags.items():
        if 0 <= int(lvl) < 64:
            tab[16 * int(lvl):16 * int(lvl) + 16] = bytes(b)[:16].ljust(16, b'\0')
    g._rh_area_flags = bytes(tab)
    lib.rh_set_flags(ctypes.c_char_p(g._rh_area_flags))
    return {int(k): [i for i in range(16) if bytes(v)[i] & 1] for k, v in flags.items() if any(x & 1 for x in bytes(v))}


def _isl_2d(g, rd):
    lib = _island_lib(g)
    return [_redirect(g, _one_call(g, 'read_controller_inputs', 'adjust_analog_stick'),
                      ctypes.cast(lib.rh_adjust_analog_stick, ctypes.c_void_p).value),
            _redirect(g, _one_call(g, 'mode_8_directions_camera', 'radial_camera_input'),
                      ctypes.cast(lib.rh_mode8_radial, ctypes.c_void_p).value)]


def _isl_no12k(g, rd):
    import st_patches as SP
    f0 = g.base + SP._funcs()['cur_obj_move_using_vel_and_gravity']
    code = ctypes.string_at(f0, 0xA0)
    ret = code.index(b'\xC3\x66', 0x40)
    jas = [i for i in range(len(code) - 1) if code[i] == 0x77 and i + 2 + code[i + 1] == ret]
    if len(jas) != 6:
        raise RuntimeError(f'obj_no_12k: {len(jas)} bound jumps found (expected 6): this DLL build differs')
    saved = []
    for i in jas:
        saved.append((f0 + i, code[i:i + 2]))
        _write_code(f0 + i, b'\x66\x90')
    return saved


def _isl_finish(g, rd):
    lib = _island_lib(g)
    e = g.addr('bhv_penguin_race_finish_line_update')
    stub = _jmp_abs(ctypes.cast(lib.rh_finish_line_2d, ctypes.c_void_p).value)
    old = ctypes.string_at(e, len(stub))
    _write_code(e, stub)
    return [(e, old)]


def _star_table(rd, s):
    lui, jal, ori = (_u32(rd, a) for a in s)
    if jal != 0x0C1009C0:
        return None
    t = ((lui & 0xFFFF) << 16) | (ori & 0xFFFF)
    return (_f32(rd, t), _f32(rd, t + 4), _f32(rd, t + 8))


def _isl_stars(g, rd):
    a = g.addr
    saved = []
    king = _star_table(rd, STAR_KING)
    if king:
        site = _one_call(g, 'king_bobomb_act_7', 'cur_obj_spawn_star_at_y_offset')
        st = _stub(g, _set_xmm(0, king[0]) + _set_xmm(1, king[1]) + _set_xmm(2, king[2]) + _jmp_abs(a('spawn_default_star')))
        saved.append((g.base + site, ctypes.string_at(g.base + site, 5)))
        _write_code(g.base + site, bytes.fromhex('E8') + struct.pack('<i', st - (g.base + site + 5)))
    bully = _star_table(rd, STAR_BULLY)
    if bully:
        site = _one_call(g, 'bully_act_level_death', 'spawn_star', pick=lambda s: _loads_before(g, s).get(3) == -6800.0)
        st = _stub(g, _set_xmm(1, bully[0]) + _set_xmm(2, bully[1]) + _set_xmm(3, bully[2]) + _jmp_abs(a('spawn_star')))
        saved.append((g.base + site, ctypes.string_at(g.base + site, 5)))
        _write_code(g.base + site, bytes.fromhex('E8') + struct.pack('<i', st - (g.base + site + 5)))
    w0, w1 = _u32(rd, STAR_WHOMP[0]), _u32(rd, STAR_WHOMP[1])
    whomp = None
    if w0 != 0x3C014334 and w0 >> 16 == 0x3C01 and w1 >> 16 == 0xC42C:
        hi = (w0 & 0xFFFF) << 16
        lo = lambda w: (w & 0xFFFF) - 0x10000 if w & 0x8000 else w & 0xFFFF
        t = [hi + lo(_u32(rd, STAR_WHOMP[1] + 4 * k)) for k in range(3)]
        whomp = (_f32(rd, t[0]), _f32(rd, t[1]), _f32(rd, t[2]))
        site = _one_call(g, 'whomp_die', 'spawn_default_star')
        st = _stub(g, _set_xmm(0, whomp[0]) + _set_xmm(1, whomp[1]) + _set_xmm(2, whomp[2]) + _jmp_abs(a('spawn_default_star')))
        saved.append((g.base + site, ctypes.string_at(g.base + site, 5)))
        _write_code(g.base + site, bytes.fromhex('E8') + struct.pack('<i', st - (g.base + site + 5)))
    g._rh_boss_stars = {'king': king, 'bully': bully, 'whomp': whomp}
    return saved


def _isl_whomp_scale(g, rd):
    s = struct.unpack('>f', struct.pack('>I', (_u32(rd, WHOMP_SCALE) & 0xFFFF) << 16))[0]
    site = _one_call(g, 'whomp_init', 'cur_obj_scale', pick=lambda x: _loads_before(g, x).get(0) == 2.0)
    st = _stub(g, _set_xmm(0, s) + _jmp_abs(g.addr('cur_obj_scale')))
    old = ctypes.string_at(g.base + site, 5)
    _write_code(g.base + site, bytes.fromhex('E8') + struct.pack('<i', st - (g.base + site + 5)))
    g._rh_whomp_scale = s
    return [(g.base + site, old)]


def _isl_fov(g, rd):
    import st_patches as SP
    v = struct.unpack('>f', struct.pack('>I', (_u32(rd, FOV_CONST) & 0xFFFF) << 16))[0]
    F = SP._funcs()
    import bisect
    s = F['_sorted']
    f0 = F['geo_process_node_and_siblings']
    code = ctypes.string_at(g.base + f0, s[bisect.bisect_right(s, f0)] - f0)
    hits = []
    for i in range(len(code) - 16):
        if code[i:i + 3] == b'\xF3\x0F\x59' and code[i + 3] & 0xC7 == 0x05:
            d = struct.unpack_from('<i', code, i + 4)[0]
            c = g.base + f0 + i + 8 + d
            if struct.unpack('<f', ctypes.string_at(c, 4))[0] == 32768.0 and code[i + 8:i + 11] == b'\xF3\x0F\x5E':
                d2 = struct.unpack_from('<i', code, i + 12)[0]
                if code[i + 11] & 0xC7 == 0x05 and struct.unpack('<f', ctypes.string_at(g.base + f0 + i + 16 + d2, 4))[0] == 180.0:
                    hits.append(i)
    if len(hits) != 1:
        raise RuntimeError(f'obj_fov: {len(hits)} sites (expected 1): this DLL build differs')
    i = hits[0]
    cst = _stub(g, struct.pack('<f', v))
    a = g.base + f0 + i
    old = ctypes.string_at(a + 4, 4)
    _write_code(a + 4, struct.pack('<i', cst - (a + 8)))
    g._rh_fov_const = v
    return [(a + 4, old)]


def _isl_actsel(g, rd):
    tab = 0x80000000 | ((_u32(rd, 0x80402F08) & 0xFFFF) << 16) | (_u32(rd, 0x80402F0C) & 0xFFFF)
    table = bytes(rd[tab - 0x80000000:tab - 0x80000000 + 64])
    e = g.addr('lvl_set_current_level')
    pro = ctypes.string_at(e, 6)
    if pro != bytes.fromhex('5653 4883EC28'.replace(' ', '')):
        raise RuntimeError(f'act_select_table: unexpected host prologue {pro.hex()}: this DLL build differs')
    tramp = _stub(g, pro + _jmp_abs(e + 6))
    data = _stub(g, table)
    a = g.addr
    H = bytes.fromhex
    Q = lambda v: struct.pack('<Q', v)
    rcx = lambda v: H('48B9') + Q(v)
    prog = [H('49B8') + Q(a('sWarpCheckpointActive')), H('450FB608'), H('4151'), H('4883EC20'),
            H('48B8') + Q(tramp), H('FFD0'), H('4883C420'), H('4159'),
            H('4183F902'), ('j', 0x74, 'zero'),
            rcx(a('gCurrDemoInput')), H('48833900'), ('j', 0x75, 'ret'),
            rcx(a('gCurrCreditsEntry')), H('48833900'), ('j', 0x75, 'ret'),
            rcx(a('gCurrCourseNum')), H('66833901'), ('j', 0x7C, 'ret'),
            rcx(a('gCurrLevelNum')), H('480FBF11'), H('83E23F'),
            rcx(data), H('0FB60411'), H('83F801'), ('j', 0x75, 'zero'),
            rcx(a('gDebugLevelSelect')), H('803900'), ('j', 0x74, 'one'),
            rcx(a('gShowProfiler')), H('803900'), ('j', 0x74, 'zero'),
            ('L', 'one'), H('B801000000C3'), ('L', 'zero'), H('31C0C3'), ('L', 'ret'), H('C3')]
    pos, labels = 0, {}
    for it in prog:
        if isinstance(it, tuple) and it[0] == 'L':
            labels[it[1]] = pos
        else:
            pos += 2 if isinstance(it, tuple) else len(it)
    code, pos = b'', 0
    for it in prog:
        if isinstance(it, tuple):
            if it[0] == 'j':
                code += bytes([it[1], (labels[it[2]] - (pos + 2)) & 0xFF])
                pos += 2
        else:
            code += it
            pos += len(it)
    w = _stub(g, code)
    old = ctypes.string_at(e, 5)
    _write_code(e, H('E9') + struct.pack('<i', w - (e + 5)))
    g._rh_actsel = {'table': list(table[:40]), 'wrapper': w}
    return [(e, old)]


def _approach(g, rd):
    T = struct.unpack('>f', struct.pack('>I', (_u32(rd, APPROACH_T) & 0xFFFF) << 16))[0]
    V = struct.unpack('>f', struct.pack('>I', (_u32(rd, APPROACH_V) & 0xFFFF) << 16))[0]
    tab = json.load(open(APPROACH_SITES))
    fm = lambda m: V if T < m else m
    saved, n_const = [], 0
    for c in tab['const']:
        a = g.base + c['at']
        n = len(c['insn']) // 2
        if ctypes.string_at(a, n) != bytes.fromhex(c['insn']):
            raise RuntimeError(f"camera_approach: unexpected code at +{c['at']:#x} ({c['fn']}): this DLL build differs "
                               '(romhack/_approach_sites.json matches the shipped sm64_us.dll only)')
        k = c['width'] // 4
        cur = struct.unpack('<%df' % k, ctypes.string_at(g.base + c['const'], c['width']))
        new = tuple(struct.unpack('<f', struct.pack('<f', fm(v)))[0] for v in cur)
        if new == cur:
            continue
        cst = _stub(g, struct.pack('<%df' % k, *new))
        d = a + c['disp_at']
        saved.append((d, ctypes.string_at(d, 4)))
        _write_code(d, struct.pack('<i', cst - (a + n)))
        n_const += 1
    page, pos = _alloc_near(g.base, 0x2000), 0
    for m in tab['min']:
        a = g.base + m['at']
        if ctypes.string_at(a, m['len']) != bytes.fromhex(m['insn']):
            raise RuntimeError(f"camera_approach: unexpected code at +{m['at']:#x} ({m['fn']}): this DLL build differs "
                               '(romhack/_approach_sites.json matches the shipped sm64_us.dll only)')
        dat = m['data_at']
        code = bytearray.fromhex(m['stub']) + bytes(20)
        struct.pack_into('<f', code, dat['ONE'], 1.0)
        struct.pack_into('<f', code, dat['T'], T)
        struct.pack_into('<f', code, dat['V'], V)
        struct.pack_into('<Q', code, dat['RET'], g.base + m['resume'])
        st = page + pos
        ctypes.memmove(st, bytes(code), len(code))
        pos += (len(code) + 15) & ~15
        saved.append((a, ctypes.string_at(a, m['len'])))
        _write_code(a, bytes.fromhex('E9') + struct.pack('<i', st - (a + 5)) + b'\x90' * (m['len'] - 5))
    g._rh_approach = {'T': T, 'V': V, 'const_sites': n_const, 'min_sites': len(tab['min']), 'page': page}
    return saved


ISLAND_CODE = {'island_2d': _isl_2d, 'act_select_table': _isl_actsel, 'obj_no_12k': _isl_no12k, 'finish_line_2d': _isl_finish,
               'boss_star_pos': _isl_stars, 'whomp_scale': _isl_whomp_scale, 'obj_fov': _isl_fov,
               'camera_approach': _approach}


def _w(rd, a):
    return struct.unpack_from('>I', rd, a - 0x80000000)[0]


def _s16(x):
    return x - 0x10000 if x & 0x8000 else x


def _jmp_stub(g, site, n, code, resume):
    a = g.base + site
    old = ctypes.string_at(a, n)
    st = _stub(g, code + bytes(5))
    ctypes.memmove(st + len(code), bytes.fromhex('E9') + struct.pack('<i', g.base + resume - (st + len(code) + 5)), 5)
    _write_code(a, bytes.fromhex('E9') + struct.pack('<i', st - (a + 5)) + b'\x90' * (n - 5))
    return (a, old)


def _host_check(g, fn, off, expect):
    import st_patches as SP
    a = g.base + SP._funcs()[fn] + off
    if ctypes.string_at(a, len(expect) // 2) != bytes.fromhex(expect):
        raise RuntimeError(f'{fn}+{off:#x}: unexpected host code (this DLL build differs)')
    return a


def _rm_slide_kick(g, rd):
    import st_patches as SP
    mask = _w(rd, 0x80268350) & 0xFFFF
    f0 = SP._funcs()['act_slide_kick_slide']
    _host_check(g, 'act_slide_kick_slide', 8, 'f64102027572')
    jump = g.base + f0 + 0x0e + 0x72
    st_code = bytes.fromhex('66F74102') + struct.pack('<H', mask) + bytes.fromhex('0F85')
    a = g.base + f0 + 8
    old = ctypes.string_at(a, 6)
    st = _stub(g, st_code + bytes(4) + bytes(5))
    ctypes.memmove(st + len(st_code), struct.pack('<i', jump - (st + len(st_code) + 4)), 4)
    e = st + len(st_code) + 4
    ctypes.memmove(e, bytes.fromhex('E9') + struct.pack('<i', g.base + f0 + 0x0e - (e + 5)), 5)
    _write_code(a, bytes.fromhex('E9') + struct.pack('<i', st - (a + 5)) + b'\x90')
    return [(a, old)]


def _rm_air_wall(g, rd):
    A = _s16(_w(rd, 0x80256488) & 0xFFFF)
    B = _s16(_w(rd, 0x80256494) & 0xFFFF) - 1
    a = _host_check(g, 'perform_air_quarter_step', 0x2a2, '66050060663d00c0')
    old = ctypes.string_at(a, 8)
    _write_code(a, bytes.fromhex('6605') + struct.pack('<H', (-A) & 0xFFFF) + bytes.fromhex('663D')
                + struct.pack('<H', (B - A) & 0xFFFF))
    return [(a, old)]


def _rm_1up(g, rd):
    import st_patches as SP
    v = _w(rd, 0x802F3C9C) & 0xFFFF
    saved = []
    for fn, off in (('bhv_1up_interact', 0x4d), ('bhv_1up_loop', 0xae)):
        _host_check(g, fn, off, '8080e900000001')
        site = SP._funcs()[fn] + off
        saved.append(_jmp_stub(g, site, 7, bytes.fromhex('66C780EA000000') + struct.pack('<H', v), site + 7))
    return saved


def _rm_star_table(g, rd):
    hi, lo = _w(rd, 0x802A55E4) & 0xFFFF, _w(rd, 0x802A55EC) & 0xFFFF
    t = (hi << 16) | lo
    pos = [_f32(rd, t + 4 * k) for k in range(3)]
    site = _one_call(g, 'cur_obj_spawn_star_at_y_offset', 'spawn_default_star')
    st = _stub(g, _set_xmm(0, pos[0]) + _set_xmm(1, pos[1]) + _set_xmm(2, pos[2]) + _jmp_abs(g.addr('spawn_default_star')))
    old = ctypes.string_at(g.base + site, 5)
    _write_code(g.base + site, bytes.fromhex('E8') + struct.pack('<i', st - (g.base + site + 5)))
    g._rh_star_table = pos
    return [(g.base + site, old)]


def _slti_pair(rd):
    w1, w2 = _w(rd, 0x80256488), _w(rd, 0x80256494)
    return w1 >> 16 == 0x29E1 and w2 >> 16 == 0x29E1 and (w1, w2) != (0x29E1A000, 0x29E16001)


RM_TWEAKS = {
    'rm_no_fall_damage': {'sig': {0x8026A2F4: 0x10000018, 0x8026A368: 0x1000001C},
                          'host': [('check_fall_damage', 0, '565348', '31c0c3')]},
    'rm_no_far_fall_sound': {'sig': {0x8026A044: 0x1000000C}, 'host': [('play_far_fall_sound', 0x3a, '770c', '9090')]},
    'rm_no_water_health': {'sig': {0x80254180: 0, 0x802541B8: 0},
                           'host': [('update_mario_health', 0x45, '0f8405010000', '909090909090')]},
    'rm_star_no_exit': {'sig': {0x8024DC50: 0x24080001},
                        'host': [('interact_star_or_key', 0x9e, '4181e400040000', '4181cc00040000')]},
    'rm_ledge_no_letgo': {'sig': {0x8025F758: 0x10000005}, 'host': [('act_ledge_grab', 0x45, '0f87ec000000', '909090909090')]},
    'rm_cam_mario_active': {'sig': {0x802872C0: 0x316C0005, 0x80287BC4: 0x240E0005},
                            'host': [('init_camera', 0x64, '668325aa26cd0004', '668325aa26cd0005'),
                                     ('select_mario_cam_mode', 0, 'b804000000', 'b805000000')]},
    'rm_cam_reset_zoomed': {'sig': {0x80287038: 0x24080002, 0x8028703C: 0, 0x80287044: 0xA428C848},
                            'host': [('reset_camera', 0xbe, '41bb00080000', '41bb02080000'),
                                     ('reset_camera', 0x18e, 'c7053cbecd00ffffffff', '90' * 10)]},
    'rm_pause_no_cam_flag': {'sig': {0x8024B5A4: 0}, 'host': [('play_mode_normal', 0x1d7, '6681080080', '9090909090')]},
    'rm_infinite_lives': {'sig': {0x8024AB10: 0x24080001, 0x8024AB98: 0x240E0001},
                          'host': [('level_trigger_warp', 0x57, '750e', 'eb0e'), ('level_trigger_warp', 0x487, '7525', 'eb25')]},
    'rm_slide_kick_b': {'sig': lambda rd: _w(rd, 0x80268350) >> 16 == 0x31F8 and _w(rd, 0x80268350) != 0x31F80002,
                        'host': _rm_slide_kick},
    'rm_air_wall_angle': {'sig': _slti_pair, 'host': _rm_air_wall},
    'rm_1up_heals': {'sig': {0x802F3C98: 0x81CF00AE, 0x802F3CA0: 0xA5CF00AE}, 'host': _rm_1up},
    'rm_star_y_offset_table': {'sig': lambda rd: (_w(rd, 0x802A55E8) == 0x0C1009C0 and _w(rd, 0x802A55E4) >> 16 == 0x3C04
                                                  and _w(rd, 0x802A55EC) >> 16 == 0x3484
                                                  and all(_w(rd, a) == x for a, x in STAR_HELPER.items())),
                               'host': _rm_star_table},
}


def rm_detect(rd):
    out = []
    for name, t in RM_TWEAKS.items():
        s = t['sig']
        if (s(rd) if callable(s) else all(_w(rd, a) == w for a, w in s.items())):
            out.append(name)
    return out


def _rm_apply(g, name, rd):
    h = RM_TWEAKS[name]['host']
    if callable(h):
        return h(g, rd)
    saved = []
    for fn, off, expect, new in h:
        a = _host_check(g, fn, off, expect)
        saved.append((a, ctypes.string_at(a, len(new) // 2)))
        _write_code(a, bytes.fromhex(new))
    return saved


def apply(g, name='bounds4', rd=None):
    if name in RM_TWEAKS:
        done = g.__dict__.setdefault('_rh_rm', {})
        if name not in done:
            if rd is None:
                raise ValueError(f'{name}: rd (the .st RDRAM) needed')
            done[name] = _rm_apply(g, name, rd)
        return [name]
    if name == 'camera_walls4':
        return ['camera_walls4'] if getattr(g, '_rh_camwalls', None) else _apply_camwalls(g)
    if name == 'camera_default_yaw0':
        if getattr(g, '_rh_camyaw', None):
            return ['camera_default_yaw0']
        import st_patches as SP
        sites = SP._calls_to(g, 'update_default_camera', 'camera_approach_s16_symmetric_bool')
        if not sites:
            raise RuntimeError('camera_default_yaw0: no call to camera_approach_s16_symmetric_bool in update_default_camera')
        saved = []
        for off in sites:
            saved.append((g.base + off, ctypes.string_at(g.base + off, 5)))
            _write_code(g.base + off, bytes.fromhex('31C00F1F00'))
        g._rh_camyaw = saved
        return ['camera_default_yaw0']
    if name in ISLAND_CODE:
        done = g.__dict__.setdefault('_rh_island', {})
        if name not in done:
            if name in ('boss_star_pos', 'whomp_scale', 'obj_fov', 'act_select_table', 'camera_approach') and rd is None:
                raise ValueError(f'{name}: rd (the .st RDRAM) needed')
            done[name] = ISLAND_CODE[name](g, rd)
        return [name]
    if name == 'camera_cside_held':
        if getattr(g, '_rh_cside', None):
            return ['camera_cside_held']
        import st_patches as SP
        f0 = g.base + SP._funcs()['handle_c_button_movement']
        ps = g.addr('play_sound')
        for off, (want, new) in CSIDE_HOST.items():
            have = ctypes.string_at(f0 + off, len(new))
            if want is None:
                ok = have[0] == 0xE8 and f0 + off + 5 + struct.unpack_from('<i', have, 1)[0] == ps
            else:
                ok = have == want
            if not ok:
                raise RuntimeError(f'camera_cside_held: unexpected host code at handle_c_button_movement+{off:#x} '
                                   f'({have.hex()}): this DLL build differs, the patch needs new offsets')
        saved = []
        for off, (want, new) in CSIDE_HOST.items():
            saved.append((f0 + off, ctypes.string_at(f0 + off, len(new))))
            _write_code(f0 + off, new)
        g._rh_cside = saved
        return ['camera_cside_held']
    if name == 'floor_highest':
        lib = getattr(g, '_rh_lib', None)
        if lib is None or not getattr(g, '_rh_patched', None):
            raise RuntimeError('floor_highest: bounds4 (rh_collision4.dll) must be applied first')
        lib.rh_set_floor_highest(1)
        if 'floor_highest' not in g._rh_patched_names:
            g._rh_patched_names = list(g._rh_patched_names) + ['floor_highest']
        return ['floor_highest']
    if name == 'camera_8dir_all':
        if getattr(g, '_rh_cam8', None):
            if rd is not None:
                _cam8_seed(g, rd)
            return ['camera_8dir_all']
        if rd is None:
            raise ValueError('camera_8dir_all: rd (the .st RDRAM) needed')
        return _apply_cam8(g, rd)
    if name == 'camera_course_table':
        if getattr(g, '_rh_camtab', None):
            return ['camera_course_table']
        if rd is None:
            raise ValueError('camera_course_table: rd (the .st RDRAM) needed')
        return _apply_camtab(g, rd)
    if name != 'bounds4':
        raise ValueError(name)
    if getattr(g, '_rh_patched', None):
        return g._rh_patched_names
    import n64mem as NM
    tmp = _dll_copy(DLL4, 'rh_collision4', g)
    lib = ctypes.CDLL(tmp)
    ob = {m[3]: m[1] for m in NM.members(NM.find_type('Object'))}
    a = g.addr
    gl = _Globals(a('gStaticSurfacePartition'), a('gDynamicSurfacePartition'), a('gCheckingSurfaceCollisionsForCamera'),
                  a('gFindFloorIncludeSurfaceIntangible'), a('gCurrentObject'), a('gMarioObject'), a('gMarioState'),
                  a('gSurfacesAllocated'), a('gSurfaceNodesAllocated'), a('sSurfacePool'), a('sSurfaceNodePool'),
                  a('gNumCalls'), a('gNumFindFloorMisses'), a('sFloorGeo'), a('bhvDDDWarp'),
                  ob['activeFlags'], ob['behavior'])
    lib.rh_init(ctypes.byref(gl))
    patched = []
    for fn in BOUNDS4_FUNCS:
        entry = a(fn)
        target = ctypes.cast(getattr(lib, 'rh_' + fn), ctypes.c_void_p).value
        stub = b'\x48\xB8' + struct.pack('<Q', target) + b'\xFF\xE0'
        patched.append((entry, ctypes.string_at(entry, len(stub))))
        _write_code(entry, stub)
    g._rh_patched, g._rh_lib, g._rh_globals, g._rh_patched_names = patched, lib, gl, [name]
    return [name]


def revert(g):
    for entry, orig in getattr(g, '_rh_patched', None) or []:
        _write_code(entry, orig)
    g._rh_patched, g._rh_patched_names = None, []
    cw = getattr(g, '_rh_camwalls', None)
    if cw:
        for a, orig in cw[0]:
            _write_code(a, orig)
        g.__dict__.setdefault('_rh_camwalls_old', []).append(cw)
        g._rh_camwalls = None
    for a, orig in getattr(g, '_rh_camyaw', None) or []:
        _write_code(a, orig)
    g._rh_camyaw = None
    isl = getattr(g, '_rh_island', None) or {}
    for name in list(isl):
        for a, orig in reversed(isl[name]):
            _write_code(a, orig)
    g._rh_island = {}
    if getattr(g, '_rh_stub_page', None):
        g._rh_stub_page[1] = 0
    for a, orig in getattr(g, '_rh_cside', None) or []:
        _write_code(a, orig)
    g._rh_cside = None
    c8 = getattr(g, '_rh_cam8', None)
    if c8:
        for a, orig in reversed(c8[0]):
            _write_code(a, orig)
        g.__dict__.setdefault('_rh_cam8_old', []).append(c8)
        g._rh_cam8 = None
    ct = getattr(g, '_rh_camtab', None)
    if ct:
        _write_code(ct[0], ct[1])
        g.__dict__.setdefault('_rh_camtab_old', []).append(ct)
        g._rh_camtab = None
    for name, saved in (getattr(g, '_rh_rm', None) or {}).items():
        for a, orig in reversed(saved):
            _write_code(a, orig)
    g._rh_rm = {}
    for a, orig in reversed(getattr(g, '_rh_dialogs', None) or []):
        _write_code(a, orig)
    g._rh_dialogs = None


DIALOG_TABLE_CODE = (0x802DA1B8, 0x802DA1C0)


def install_dialogs(g, st):
    import n64mem as NM
    rd = st.rd
    u32 = lambda a: struct.unpack_from('>I', rd, a - 0x80000000)[0]
    lui, addiu = u32(DIALOG_TABLE_CODE[0]), u32(DIALOG_TABLE_CODE[1])
    if lui >> 16 != 0x3C04 or addiu >> 16 != 0x2484:
        return 0
    tab = ((lui & 0xFFFF) << 16) + ((addiu & 0xFFFF) - 0x10000 if addiu & 0x8000 else addiu & 0xFFFF)
    segs = st.all['sSegmentTable']
    virt = lambda p: 0x80000000 + (u32(segs + 4 * (p >> 24)) & 0x7FFFFFFF) + (p & 0xFFFFFF) if p < 0x80000000 else p
    k = NM.find_type('DialogEntry')
    D = {m[3]: m[1] for m in NM.members(k)}
    size = NM.T[k]['size']
    htab = g.addr('seg2_dialog_table')
    saved, keep = [], []
    for i in range(256):
        try:
            pe = u32(virt(tab) + 4 * i)
            if not pe:
                break
            e = virt(pe)
            o = virt(u32(e + 12)) - 0x80000000
            text = bytes(rd[o:rd.index(b'\xff', o) + 1])
            lines, left, width = rd[e - 0x80000000 + 4], *struct.unpack_from('>hh', rd, e - 0x80000000 + 6)
        except (ValueError, IndexError, struct.error):
            break
        he = ctypes.c_void_p.from_address(htab + 8 * i).value
        if not he:
            break
        hs = ctypes.c_void_p.from_address(he + D['str']).value
        h_text = ctypes.string_at(hs, len(text))
        if (h_text == text and ctypes.c_byte.from_address(he + D['linesPerBox']).value == lines
                and ctypes.c_short.from_address(he + D['leftOffset']).value == left
                and ctypes.c_short.from_address(he + D['width']).value == width):
            continue
        tbuf = ctypes.create_string_buffer(text, len(text))
        ebuf = ctypes.create_string_buffer(ctypes.string_at(he, size), size)
        ea = ctypes.addressof(ebuf)
        ctypes.c_byte.from_address(ea + D['linesPerBox']).value = lines
        ctypes.c_short.from_address(ea + D['leftOffset']).value = left
        ctypes.c_short.from_address(ea + D['width']).value = width
        ctypes.c_void_p.from_address(ea + D['str']).value = ctypes.addressof(tbuf)
        keep += [tbuf, ebuf]
        saved.append((htab + 8 * i, ctypes.string_at(htab + 8 * i, 8)))
        _write_code(htab + 8 * i, struct.pack('<Q', ea))
    g._rh_dialogs = saved
    g.__dict__.setdefault('_rh_dialog_bufs', []).append(keep)
    return len(saved)
