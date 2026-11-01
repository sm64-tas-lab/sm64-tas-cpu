"""winload: the game's Windows DLLs on Linux, through a ctypes.CDLL look-alike (the native side is winload.c).

    import winload
    lib = winload.load_library('sm64_us.dll')     # ctypes.CDLL(path) on Windows, a PELibrary elsewhere
    lib.sm64_init.restype = None
    lib.sm64_init()
    base = lib._handle                            # the image base (HMODULE), as on Windows: base + RVA = the symbol

A PELibrary maps a fresh, independent copy of the image on every load (Windows shares one module per path, which is
why sm64hl.py copies the DLL per game; here the copy is not needed). Its functions take the usual ctypes argtypes /
restype / errcheck and are called with the Windows x64 convention and Windows' FPU state (x87 0x27F, MXCSR 0x1F80): the
same machine code computes the same bits. A fault inside a call raises OSError('exception: access violation reading
0x...') like ctypes on Windows; an import the DLL needs that has no exact Linux equivalent fails its call with its name.

Also: free_library(handle), protect(addr, size, win_prot) (VirtualProtect), valloc(addr, size, type, prot)
(VirtualAlloc), in_dll(ctype, lib, name) (ctype.in_dll for both kinds), unresolved() (the trapped imports so far).
Used by: sm64hl.py, st_patches.py, st_romhack.py and the moteur's tools that load a game DLL.
"""

import ctypes
import hashlib
import os
import struct
import subprocess
import sys
import threading

IS_WIN = os.name == 'nt'
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'winload.c')
BUILD = os.environ.get('WINLOAD_BUILD') or os.path.join(HERE, '_build')

_lib = None
_lib_lock = threading.Lock()


class _Fault(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_int), ('sig', ctypes.c_int), ('code', ctypes.c_int),
                ('addr', ctypes.c_uint64), ('rip', ctypes.c_uint64), ('msg', ctypes.c_char * 512)]


def _compilers():
    cc = os.environ.get('CC')
    if cc:
        yield cc.split()
    yield ['cc']
    yield ['gcc']
    yield ['clang']
    yield [sys.executable, '-m', 'ziglang', 'cc']


def build(force=False):
    """compiles winload.c into _build/libwinload_<hash>.so (once per source) -> its path"""
    src = open(SRC, 'rb').read()
    tag = hashlib.sha1(src).hexdigest()[:12]
    out = os.path.join(BUILD, 'libwinload_%s.so' % tag)
    if os.path.isfile(out) and not force:
        return out
    os.makedirs(BUILD, exist_ok=True)
    tmp = '%s.%d.tmp' % (out, os.getpid())
    errs = []
    for cc in _compilers():
        try:
            r = subprocess.run(cc + ['-O2', '-shared', '-fPIC', '-o', tmp, SRC], capture_output=True, text=True)
        except OSError as e:
            errs.append('%s: %s' % (cc[0], e))
            continue
        if r.returncode == 0:
            os.replace(tmp, out)
            for f in os.listdir(BUILD):                     # older builds of the loader
                if f.startswith('libwinload_') and f.endswith('.so') and f != os.path.basename(out):
                    try:
                        os.remove(os.path.join(BUILD, f))
                    except OSError:
                        pass
            return out
        errs.append('%s: %s' % (' '.join(cc), r.stderr.strip()[-1500:]))
    raise OSError('winload: cannot compile %s (a C compiler is needed: gcc, clang or pip ziglang)\n%s'
                  % (SRC, '\n'.join(errs)))


def native():
    """the loaded libwinload (built on first use)"""
    global _lib
    if _lib is None:
        with _lib_lock:
            if _lib is None:
                lib = ctypes.CDLL(build())
                lib.wl_load.restype = ctypes.c_void_p
                lib.wl_load.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_size_t]
                lib.wl_free.restype = ctypes.c_int
                lib.wl_free.argtypes = [ctypes.c_void_p]
                lib.wl_sym.restype = ctypes.c_void_p
                lib.wl_sym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
                lib.wl_call.restype = ctypes.c_int
                lib.wl_call.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64),
                                        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(_Fault)]
                lib.wl_call0.restype = ctypes.c_int          # (no argtypes: called with c_void_p objects, the fastest)
                lib.wl_addr.restype = ctypes.c_void_p
                lib.wl_addr.argtypes = [ctypes.c_void_p]
                lib.wl_module_of.restype = ctypes.c_void_p
                lib.wl_module_of.argtypes = [ctypes.c_void_p]
                lib.wl_unresolved.restype = ctypes.c_int
                lib.wl_unresolved.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
                lib.wl_protect.restype = ctypes.c_int
                lib.wl_protect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32,
                                           ctypes.POINTER(ctypes.c_uint32)]
                lib.wl_valloc.restype = ctypes.c_void_p
                lib.wl_valloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
                _lib = lib
    return _lib


# ---- argument and result conversion ------------------------------------------------------------------------------

_M64 = (1 << 64) - 1
_INT_TYPES = {}
for _t in (ctypes.c_byte, ctypes.c_ubyte, ctypes.c_short, ctypes.c_ushort, ctypes.c_int, ctypes.c_uint,
           ctypes.c_long, ctypes.c_ulong, ctypes.c_longlong, ctypes.c_ulonglong, ctypes.c_size_t, ctypes.c_ssize_t,
           ctypes.c_int8, ctypes.c_uint8, ctypes.c_int16, ctypes.c_uint16, ctypes.c_int32, ctypes.c_uint32,
           ctypes.c_int64, ctypes.c_uint64, ctypes.c_bool, ctypes.c_char, ctypes.c_wchar):
    _INT_TYPES[_t] = _t
_SIMPLE = ctypes._SimpleCData


def _addr_of(obj):
    """the address ctypes would pass for a pointer-like argument (byref, arrays, pointers, bytes, c_char_p ...)"""
    return native().wl_addr(obj) or 0


def _slot(a, t):
    """one argument -> its 64-bit slot (the bits the Windows callee reads from its register or stack slot)"""
    if t is not None:
        if t is ctypes.c_float:
            v = a.value if isinstance(a, _SIMPLE) else float(a)
            return struct.unpack('<I', struct.pack('<f', v))[0]
        if t is ctypes.c_double:
            v = a.value if isinstance(a, _SIMPLE) else float(a)
            return struct.unpack('<Q', struct.pack('<d', v))[0]
        if t in _INT_TYPES:
            v = a.value if isinstance(a, _SIMPLE) else a
            if isinstance(v, bytes):
                v = v[0]
            return int(v) & _M64
        if isinstance(t, type) and issubclass(t, ctypes.Structure) and isinstance(a, ctypes.Structure):
            return _struct_slot(a)
        return _addr_of(a)                                  # pointers, c_void_p, c_char_p, arrays, byref ...
    if a is None:
        return 0
    if isinstance(a, bool):
        return int(a)
    if isinstance(a, int):
        return a & _M64
    if isinstance(a, ctypes.c_float):
        return struct.unpack('<I', struct.pack('<f', a.value))[0]
    if isinstance(a, ctypes.c_double):
        return struct.unpack('<Q', struct.pack('<d', a.value))[0]
    if isinstance(a, _SIMPLE) and type(a) in _INT_TYPES:
        return int(a.value) & _M64
    if isinstance(a, ctypes.Structure):
        return _struct_slot(a)
    if isinstance(a, float):
        raise ctypes.ArgumentError("Don't know how to convert parameter (a float needs argtypes c_float / c_double)")
    if isinstance(a, str):
        raise ctypes.ArgumentError('a str argument would be a wchar_t* (16-bit on Windows): pass bytes')
    return _addr_of(a)


def _struct_slot(s):
    """a structure by value: 1, 2, 4 or 8 bytes in the slot, anything else by reference to a copy (Windows x64)"""
    n = ctypes.sizeof(s)
    if n in (1, 2, 4, 8):
        return int.from_bytes(ctypes.string_at(ctypes.addressof(s), n), 'little')
    raise ctypes.ArgumentError('structure of %d bytes by value: not supported by winload' % n)


def _result(rax, xmm, t):
    if t is None:
        return None
    if t is ctypes.c_float:
        return struct.unpack('<f', struct.pack('<I', xmm & 0xFFFFFFFF))[0]
    if t is ctypes.c_double:
        return struct.unpack('<d', struct.pack('<Q', xmm))[0]
    if t in _INT_TYPES:
        n = ctypes.sizeof(t)
        v = rax & ((1 << (8 * n)) - 1)
        if t is ctypes.c_bool:
            return bool(v & 0xFF)
        return t(v).value
    if t is ctypes.c_void_p:
        return rax or None
    if t is ctypes.c_char_p:
        return ctypes.c_char_p(rax).value if rax else None
    if isinstance(t, type) and issubclass(t, (ctypes._Pointer, ctypes.c_void_p)):
        return ctypes.cast(ctypes.c_void_p(rax), t)
    if callable(t) and not isinstance(t, type):             # restype as a callable: ctypes calls it with the int
        return t(ctypes.c_int(rax & 0xFFFFFFFF).value)
    raise TypeError('winload: restype %r not supported' % (t,))


_SIGNAMES = {11: 'access violation', 7: 'access violation (bus error)', 8: 'floating point / integer divide by zero',
             4: 'illegal instruction', 5: 'breakpoint'}


def _fault_error(f, name):
    if f.kind == 2:
        return OSError(f.msg.decode(errors='replace') + ' (in %s)' % name)
    what = _SIGNAMES.get(f.sig, 'signal %d' % f.sig)
    if f.sig in (11, 7):
        return OSError('exception: %s at 0x%016X (rip 0x%016X, in %s)' % (what, f.addr, f.rip, name))
    if f.sig == 8:
        return OSError('exception: integer divide by zero (rip 0x%016X, in %s)' % (f.rip, name))
    return OSError('exception: %s (rip 0x%016X, in %s)' % (what, f.rip, name))


class PEFunction:
    """an export of a PELibrary: callable like a ctypes function (argtypes, restype, errcheck)"""

    def __init__(self, addr, name):
        self._addr = addr
        self.__name__ = name
        self.argtypes = None
        self.restype = ctypes.c_int
        self.errcheck = None
        self._busy = False                                  # the fast path's buffers are in use (another thread)
        self._rax0 = ctypes.c_uint64()
        self._f0 = _Fault()
        self._p0 = (ctypes.c_void_p(addr), ctypes.c_void_p(ctypes.addressof(self._rax0)),
                    ctypes.c_void_p(ctypes.addressof(self._f0)))
        self._call0 = native().wl_call0

    @property
    def _as_parameter_(self):                               # ctypes.cast(fn, c_void_p).value -> the address
        return ctypes.c_void_p(self._addr)

    def __call__(self, *args):
        if not args and not self.argtypes and not self._busy and self.errcheck is None:
            self._busy = True                               # no arguments (sm64_update): preallocated buffers
            try:
                if self._call0(*self._p0):
                    raise _fault_error(self._f0, self.__name__)
                rt = self.restype
                return None if rt is None else _result(self._rax0.value, 0, rt)
            finally:
                self._busy = False
        at = self.argtypes
        if at is not None:
            if len(args) != len(at):
                raise TypeError('%s takes %d arguments (%d given)' % (self.__name__, len(at), len(args)))
            vals = [_slot(a, t) for a, t in zip(args, at)]
        else:
            vals = [_slot(a, None) for a in args]
        n = len(vals)
        slots = (ctypes.c_uint64 * max(n, 4))(*vals)
        rax, xmm, f = ctypes.c_uint64(), ctypes.c_uint64(), _Fault()
        rc = native().wl_call(self._addr, n, slots, ctypes.byref(rax), ctypes.byref(xmm), ctypes.byref(f))
        if rc:
            raise _fault_error(f, self.__name__)
        res = _result(rax.value, xmm.value, self.restype)
        if self.errcheck is not None:
            return self.errcheck(res, self, args)
        return res

    def __repr__(self):
        return '<PEFunction %s at 0x%x>' % (self.__name__, self._addr)


class PELibrary:
    """a Windows DLL loaded by winload: ctypes.CDLL's interface (attributes are the exports, _handle the base)"""

    def __init__(self, path, base=None):
        err = ctypes.create_string_buffer(1024)
        self._name = os.path.abspath(path)
        if base is None:                                    # WINLOAD_BASE=0x...: tests that need the image at one address
            base = int(os.environ.get('WINLOAD_BASE', '0'), 0)
        base = native().wl_load(os.fsencode(self._name), base, err, len(err))
        if not base:
            raise OSError(err.value.decode(errors='replace'))
        self._handle = base
        self._funcs = {}

    def _sym(self, name):
        a = native().wl_sym(self._handle, name.encode()) if self._handle else None
        if not a:
            raise AttributeError('%s: no export named %r' % (os.path.basename(self._name), name))
        return a

    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        f = self._funcs.get(name)
        if f is None:
            f = self._funcs[name] = PEFunction(self._sym(name), name)
        return f

    def __getitem__(self, name):
        return self.__getattr__(name)

    def symbol_address(self, name):
        return self._sym(name)

    def close(self):
        if self._handle:
            native().wl_free(self._handle)
            self._handle = None

    def __repr__(self):
        return "<PELibrary '%s', base 0x%x>" % (self._name, self._handle or 0)


def CFUNCTYPE(restype, *argtypes):
    """ctypes.CFUNCTYPE for a function of a game DLL called by address: CFUNCTYPE(res, *args)(addr) -> callable.
    On Windows it is ctypes.CFUNCTYPE (cdecl is the x64 convention); on Linux an address inside a winload image gets
    the Windows convention, any other address (a native .so) ctypes' own."""
    if IS_WIN:
        return ctypes.CFUNCTYPE(restype, *argtypes)
    proto = ctypes.CFUNCTYPE(restype, *argtypes)

    def make(addr):
        a = addr.value if isinstance(addr, ctypes.c_void_p) else int(addr)
        if not native().wl_module_of(a):
            return proto(a)
        f = PEFunction(a, 'fn_0x%x' % a)
        f.restype, f.argtypes = restype, list(argtypes)
        return f
    return make


def is_pe(path):
    try:
        with open(path, 'rb') as f:
            return f.read(2) == b'MZ'
    except OSError:
        return False


def load_library(path):
    """ctypes.CDLL on Windows; a PELibrary for a Windows DLL elsewhere; ctypes.CDLL for a native shared object"""
    if IS_WIN or not is_pe(path):
        return ctypes.CDLL(path)
    return PELibrary(path)


def free_library(handle_or_lib):
    """FreeLibrary: a handle (the base) or a library object -> True when it was unloaded"""
    if IS_WIN:
        h = getattr(handle_or_lib, '_handle', handle_or_lib)
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.FreeLibrary.argtypes = [ctypes.c_void_p]
        return bool(k32.FreeLibrary(ctypes.c_void_p(h)))
    if isinstance(handle_or_lib, PELibrary):
        ok = bool(handle_or_lib._handle)
        handle_or_lib.close()
        return ok
    return bool(native().wl_free(ctypes.c_void_p(handle_or_lib)))


def protect(addr, size, prot):
    """VirtualProtect(addr, size, prot) -> the old protection"""
    old = ctypes.c_uint32()
    if IS_WIN:
        k32 = ctypes.windll.kernel32
        if not k32.VirtualProtect(ctypes.c_void_p(addr), size, prot, ctypes.byref(old)):
            raise OSError('VirtualProtect failed at 0x%x' % addr)
        return old.value
    if not native().wl_protect(addr, size, prot, ctypes.byref(old)):
        raise OSError('mprotect failed at 0x%x (%d bytes)' % (addr, size))
    return old.value


def write_code(addr, data):
    """writes machine code over a loaded image (VirtualProtect RWX, write, protection back)"""
    old = protect(addr, len(data), 0x40)
    ctypes.memmove(addr, data, len(data))
    protect(addr, len(data), old)
    if IS_WIN:
        k32 = ctypes.windll.kernel32
        k32.FlushInstructionCache(k32.GetCurrentProcess(), None, 0)


def valloc(addr, size, type_=0x3000, prot=0x40):
    """VirtualAlloc(addr, size, type, prot) -> the address or None"""
    if IS_WIN:
        k32 = ctypes.windll.kernel32
        k32.VirtualAlloc.restype = ctypes.c_void_p
        k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
        return k32.VirtualAlloc(ctypes.c_void_p(addr), size, type_, prot)
    return native().wl_valloc(addr, size, type_, prot)


def in_dll(ctype, lib, name):
    """ctype.in_dll(lib, name) for a ctypes library and for a PELibrary"""
    if isinstance(lib, PELibrary):
        return ctype.from_address(lib.symbol_address(name))
    return ctype.in_dll(lib, name)


def pid_alive(pid):
    """the process pid still runs"""
    if IS_WIN:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_uint32()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _Fn:
    """a kernel32 function of the Linux stand-in: restype / argtypes accepted (and ignored) as ctypes allows"""

    def __init__(self, fn):
        self._fn, self.restype, self.argtypes, self.errcheck = fn, None, None, None

    def __call__(self, *a):
        return self._fn(*a)


def _int(x):
    if x is None:
        return 0
    if isinstance(x, int):
        return x
    v = getattr(x, 'value', None)
    if isinstance(v, int) or v is None:
        return v or 0
    return _addr_of(x)


def _out_u32(ref, value):
    if ref is not None:
        ctypes.c_uint32.from_address(_addr_of(ref)).value = value


class _Kernel32:
    """the few kernel32 calls the tools make on a loaded game DLL, on Linux (VirtualProtect, VirtualAlloc, ...)"""

    def __init__(self):
        def virtual_protect(addr, size, prot, old_ref):
            old = ctypes.c_uint32()
            ok = native().wl_protect(_int(addr), _int(size), _int(prot), ctypes.byref(old))
            _out_u32(old_ref, old.value)
            return int(bool(ok))

        def virtual_alloc(addr, size, type_, prot):
            return native().wl_valloc(_int(addr), _int(size), _int(type_), _int(prot))

        def open_process(access, inherit, pid):
            return _int(pid) + 1 if pid_alive(_int(pid)) else 0

        def exit_code(h, ref):
            _out_u32(ref, 259 if pid_alive(_int(h) - 1) else 0)
            return 1

        self.VirtualProtect = _Fn(virtual_protect)
        self.VirtualAlloc = _Fn(virtual_alloc)
        self.FlushInstructionCache = _Fn(lambda *a: 1)
        self.GetCurrentProcess = _Fn(lambda: -1)
        self.OpenProcess = _Fn(open_process)
        self.GetExitCodeProcess = _Fn(exit_code)
        self.CloseHandle = _Fn(lambda h: 1)

    def __getattr__(self, name):
        raise AttributeError('winload: kernel32.%s has no Linux stand-in' % name)


_K32 = None


def kernel32():
    """ctypes.windll.kernel32 on Windows; on Linux an object with the same calls for memory the game DLL uses"""
    global _K32
    if IS_WIN:
        return ctypes.windll.kernel32
    if _K32 is None:
        _K32 = _Kernel32()
    return _K32


def unresolved():
    """the imports bound to a trap so far, as 'DLL!name'"""
    if IS_WIN:
        return []
    buf = ctypes.create_string_buffer(1 << 20)
    native().wl_unresolved(buf, len(buf))
    return [x for x in buf.value.decode().split('\n') if x]
