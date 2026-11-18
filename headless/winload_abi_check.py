"""Checks that the shims the game DLL calls keep the registers Windows expects kept (rbx rbp rdi rsi r12-r15,
xmm6-15). A shim that loses one corrupts the DLL's code at random, depending on where ASLR put the loader (#21).

    python headless/winload_abi_check.py        (exit code 1 when a shim loses a register)

Each shim is called many times, since what a register ends up holding can depend on the TLS state.
"""
import ctypes
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import winload  # noqa: E402

REGS = ['rbx', 'rbp', 'rdi', 'rsi', 'r12', 'r13', 'r14', 'r15'] + ['xmm%d' % i for i in range(6, 16)]


def build_probe(out_dir):
    out = os.path.join(out_dir, 'libwinload_abi_probe.so')
    cc = (os.environ.get('CC') or 'cc').split()
    subprocess.run(cc + ['-O2', '-shared', '-fPIC', '-o', out, os.path.join(HERE, 'winload_abi_probe.c')], check=True)
    return ctypes.CDLL(out)


def main():
    lib = ctypes.CDLL(winload.build())
    lib.wl_shim.restype = ctypes.c_void_p
    lib.wl_shim.argtypes = [ctypes.c_char_p]
    with tempfile.TemporaryDirectory() as d:
        probe = build_probe(d)
        probe.wl_abi_probe.restype = ctypes.c_uint64
        probe.wl_abi_probe.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64]
        missing = ctypes.create_string_buffer(b'no-such-module.dll')
        calls = [                                       # the shims that read or write the loader's thread state
            ('_errno', 0, 0, 0),
            ('GetLastError', 0, 0, 0),
            ('SetLastError', 5, 0, 0),
            ('TlsGetValue', 0, 0, 0),
            ('TlsSetValue', 0, 0, 0),
            ('GetModuleHandleA', ctypes.addressof(missing), 0, 0),
            ('VirtualAlloc', 0, 0, 0),                  # size 0: fails and sets the last error
        ]
        bad = 0
        for name, a0, a1, a2 in calls:
            fn = lib.wl_shim(name.encode())
            if not fn:
                print('%-18s not bound to a shim' % name)
                bad += 1
                continue
            lost = 0
            for _ in range(64):
                lost |= probe.wl_abi_probe(fn, a0, a1, a2)
            names = [r for i, r in enumerate(REGS) if lost >> i & 1]
            print('%-18s %s' % (name, 'ok' if not names else 'LOSES ' + ' '.join(names)))
            bad += bool(names)
    print('%d shim(s) lose a register Windows expects kept' % bad if bad else 'every shim keeps them')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
