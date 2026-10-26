"""Set this repo up for an AI to play SM64 headless (INSTALL.bat runs this).

1. the Python packages (numpy, lz4);
2. your own ROM: Super Mario 64 US (JP optional) unlocks the game DLLs (*.dll.locked: never shipped in clear);
3. the decompilation's sources (headers only are read: the object fields), from n64decomp/sm64 (no game assets);
4. Mupen64-rr-lua (optional: replay / check the movies the AI makes);
5. the MCP server for the AI: Claude Code (and its skill), Gemini CLI when installed, mcp.json for any other client.

    python install.py [--rom-us ROM] [--rom-jp ROM] [--mupen EXE] [--no-register] [--yes]
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'headless'))
DECOMP_GIT = 'https://github.com/n64decomp/sm64.git'
DECOMP_ZIP = 'https://codeload.github.com/n64decomp/sm64/zip/refs/heads/master'
SERVER = os.path.join(ROOT, 'agent', 'sm64_headless_mcp.py')


def say(msg):
    print(msg, flush=True)


def ask(question, default=None, yes=False):
    if yes:
        return default
    try:
        a = input('%s%s: ' % (question, ' [%s]' % default if default else '')).strip().strip('"')
    except EOFError:
        return default
    return a or default


def packages():
    say('[1/5] Python packages: numpy, lz4')
    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', 'numpy', 'lz4'])
    if r.returncode:
        say('  pip failed: install numpy and lz4 by hand, then run INSTALL.bat again')
        return False
    return True


def unlock(rom, version):
    import rom_lock
    data = rom_lock.rom_z64(open(rom, 'rb').read())
    if rom_lock.rom_version(data) != version:
        say('  %s is not the original Super Mario 64 %s' % (rom, version.upper()))
        return False
    for rel in rom_lock.LOCKED[version]:
        src = os.path.join(ROOT, rel + '.locked')
        if os.path.isfile(src):
            open(os.path.join(ROOT, rel), 'wb').write(rom_lock.unlock(open(src, 'rb').read(), data))
    say('  the %s DLLs unlocked' % version.upper())
    return True


def decomp(cfg):
    say('[3/5] the decompilation\'s sources (n64decomp/sm64, code only)')
    dst = cfg.get('decomp') or os.path.join(ROOT, 'deps', 'sm64-decomp')
    if os.path.isfile(os.path.join(dst, 'include', 'object_fields.h')):
        say('  already there: %s' % dst)
        cfg['decomp'] = dst
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if shutil.which('git'):
        r = subprocess.run(['git', 'clone', '--depth', '1', '--quiet', DECOMP_GIT, dst])
        if r.returncode == 0:
            cfg['decomp'] = dst
            return True
    say('  downloading the sources as a zip (git not found or failed)')
    data = urllib.request.urlopen(DECOMP_ZIP, timeout=300).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        top = z.namelist()[0].split('/')[0]
        tmp = dst + '_tmp'
        z.extractall(tmp)
    shutil.move(os.path.join(tmp, top), dst)
    shutil.rmtree(tmp, ignore_errors=True)
    cfg['decomp'] = dst
    return True


def register(python):
    say('[5/5] the MCP server for your AI')
    entry = {'command': python, 'args': [SERVER]}
    json.dump({'mcpServers': {'sm64-headless': entry}}, open(os.path.join(ROOT, 'mcp.json'), 'w'), indent=2)
    say('  mcp.json written (for any MCP client): %s' % os.path.join(ROOT, 'mcp.json'))
    claude = shutil.which('claude')
    if claude:
        subprocess.run([claude, 'mcp', 'remove', '--scope', 'user', 'sm64-headless'], capture_output=True)
        r = subprocess.run([claude, 'mcp', 'add', '--scope', 'user', 'sm64-headless', '--', python, SERVER],
                           capture_output=True, text=True)
        say('  Claude Code: %s' % ('sm64-headless added' if r.returncode == 0 else 'failed: ' + (r.stderr or '')[-200:]))
        skill = os.path.join(os.path.expanduser('~'), '.claude', 'skills', 'sm64-headless')
        os.makedirs(skill, exist_ok=True)
        shutil.copy(os.path.join(ROOT, 'agent', 'SKILL.md'), os.path.join(skill, 'SKILL.md'))
        say('  Claude Code skill: %s' % skill)
    else:
        say('  Claude Code not found (claude): add it later with: claude mcp add sm64-headless -- "%s" "%s"' % (python, SERVER))
    gem = os.path.join(os.path.expanduser('~'), '.gemini', 'settings.json')
    if shutil.which('gemini') or os.path.isfile(gem):
        try:
            s = json.load(open(gem, encoding='utf-8')) if os.path.isfile(gem) else {}
        except ValueError:
            s = None
        if s is not None:
            s.setdefault('mcpServers', {})['sm64-headless'] = entry
            os.makedirs(os.path.dirname(gem), exist_ok=True)
            json.dump(s, open(gem, 'w', encoding='utf-8'), indent=2)
            say('  Gemini CLI: sm64-headless added to %s' % gem)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rom-us')
    ap.add_argument('--rom-jp')
    ap.add_argument('--mupen')
    ap.add_argument('--no-register', action='store_true')
    ap.add_argument('--yes', action='store_true', help='no questions: only the arguments given')
    a = ap.parse_args()
    if sys.version_info < (3, 9) or sys.maxsize < 2 ** 32:
        say('Python 3.9 or newer, 64-bit, is needed (this one: %s)' % sys.version.split()[0])
        return 1
    cfg_path = os.path.join(ROOT, 'config.json')
    try:
        cfg = json.load(open(cfg_path, encoding='utf-8'))
    except (OSError, ValueError):
        cfg = {}
    if not packages():
        return 1
    say('[2/5] your ROM (never copied anywhere, only read to unlock the DLLs)')
    rom = a.rom_us or ask('  Super Mario 64 (USA) .z64 / .n64 / .v64 path', cfg.get('rom_us'), a.yes)
    if not rom or not os.path.isfile(rom) or not unlock(rom, 'us'):
        say('  the US ROM is needed: run INSTALL.bat again with its path')
        return 1
    cfg['rom_us'] = os.path.abspath(rom)
    jp = a.rom_jp or ask('  Super Mario 64 (Japan) ROM path (optional, Enter to skip)', cfg.get('rom_jp'), a.yes)
    if jp and os.path.isfile(jp) and unlock(jp, 'jp'):
        cfg['rom_jp'] = os.path.abspath(jp)
    if not decomp(cfg):
        return 1
    say('[4/5] Mupen64-rr-lua (optional: replay and check the movies)')
    mp = a.mupen or ask('  mupen64.exe path (Enter to skip)', cfg.get('mupen'), a.yes)
    if mp and os.path.isfile(mp):
        cfg['mupen'] = os.path.abspath(mp)
    json.dump(cfg, open(cfg_path, 'w', encoding='utf-8'), indent=2)
    if not a.no_register:
        register(sys.executable)
    say('\nDone. Check it: python agent/sm64_headless_mcp.py --demo')
    say('Then ask your AI, e.g.: "start Super Mario 64 from power-on with sm64-headless, scan around Mario and make '
        'him long jump toward the castle".')
    return 0


if __name__ == '__main__':
    sys.exit(main())
