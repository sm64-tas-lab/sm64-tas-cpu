"""Lock / unlock the game DLLs with the user's own ROM (the way Wafel ships its libsm64 DLLs: *.dll.locked).

The DLLs are the decompiled game compiled natively, so they hold the game's data: they are never distributed as
they are. A .locked file is the DLL compressed (zlib) then encrypted with a key derived from the ROM itself (scrypt),
with an HMAC: without the right ROM it is useless, and a wrong ROM or a damaged file is refused (never a broken DLL).
Python's standard library only.

    python rom_lock.py unlock ROM.z64 [--dir REPO]        (the installer does this)
    python rom_lock.py lock ROM.z64 sm64_us.dll ...       (the maintainer, once per new DLL)
"""
import argparse
import hashlib
import hmac
import json
import os
import struct
import sys
import zlib

MAGIC = b'SM64LOCK1\n'
# the files locked with each ROM (paths relative to the repo's root)
LOCKED = {
    'us': ['sm64_us.dll', 'headless/romhack/rh_camera4.dll', 'headless/romhack/rh_camera8.dll',
           'headless/romhack/rh_collision4.dll', 'headless/romhack/rh_island.dll'],
    'jp': ['sm64_jp.dll'],
}
ROM_MD5 = {'us': '20b854b239203baf6c961b850a4a51a2', 'jp': '85d61f5525af708c9f1e84dce6dc10e9'}


def rom_z64(data):
    """the ROM in big-endian (.z64) order, whatever its dump's byte order (.z64 / .v64 / .n64)"""
    head = data[:4]
    if head == b'\x80\x37\x12\x40':
        return data
    if head == b'\x37\x80\x40\x12':                     # .v64: 16-bit words swapped
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b)
    if head == b'\x40\x12\x37\x80':                     # .n64: 32-bit words little-endian
        n = len(data) // 4
        return struct.pack('>%dI' % n, *struct.unpack('<%dI' % n, data[:4 * n]))
    raise ValueError('not an N64 ROM (unknown header %s)' % head.hex())


def rom_version(rom):
    md5 = hashlib.md5(rom).hexdigest()
    for v, m in ROM_MD5.items():
        if m == md5:
            return v
    return None


def _keys(rom, salt):
    k = hashlib.scrypt(rom, salt=salt, n=1 << 14, r=8, p=1, maxmem=64 << 20, dklen=64)
    return k[:32], k[32:]


def lock(data, rom):
    salt = os.urandom(16)
    ke, km = _keys(rom, salt)
    plain = zlib.compress(data, 9)
    stream = hashlib.shake_256(ke + salt).digest(len(plain))
    ct = bytes(a ^ b for a, b in zip(plain, stream)) if len(plain) < 1 << 16 else _xor(plain, stream)
    tag = hmac.new(km, salt + ct, hashlib.sha256).digest()
    return MAGIC + salt + tag + ct


def _xor(a, b):
    return (int.from_bytes(a, 'little') ^ int.from_bytes(b, 'little')).to_bytes(len(a), 'little')


def unlock(blob, rom):
    if not blob.startswith(MAGIC):
        raise ValueError('not a locked file')
    p = len(MAGIC)
    salt, tag, ct = blob[p:p + 16], blob[p + 16:p + 48], blob[p + 48:]
    ke, km = _keys(rom, salt)
    if not hmac.compare_digest(tag, hmac.new(km, salt + ct, hashlib.sha256).digest()):
        raise ValueError('this ROM does not open it (another version or a modified ROM), or the file is damaged')
    stream = hashlib.shake_256(ke + salt).digest(len(ct))
    return zlib.decompress(_xor(ct, stream))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    u = sub.add_parser('unlock', help='write the DLLs from their .locked files with your ROM')
    u.add_argument('rom')
    u.add_argument('--dir', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    lk = sub.add_parser('lock', help='write .locked files (the maintainer)')
    lk.add_argument('rom')
    lk.add_argument('files', nargs='+')
    a = ap.parse_args()
    rom = rom_z64(open(a.rom, 'rb').read())
    ver = rom_version(rom)
    if a.cmd == 'lock':
        for f in a.files:
            out = f + '.locked'
            open(out, 'wb').write(lock(open(f, 'rb').read(), rom))
            print('locked %s -> %s (%.1f MB)' % (f, out, os.path.getsize(out) / 1e6))
        return 0
    if ver is None:
        print('This ROM is not the original Super Mario 64 US or JP (md5 %s): the DLLs need the original game.'
              % hashlib.md5(rom).hexdigest())
        return 1
    n = 0
    for rel in LOCKED[ver]:
        src = os.path.join(a.dir, rel + '.locked')
        if not os.path.isfile(src):
            continue
        data = unlock(open(src, 'rb').read(), rom)
        open(os.path.join(a.dir, rel), 'wb').write(data)
        n += 1
        print('unlocked %s (%.1f MB)' % (rel, len(data) / 1e6))
    print('%d file(s) unlocked with the %s ROM' % (n, ver.upper()))
    return 0 if n else 1


if __name__ == '__main__':
    sys.exit(main())
