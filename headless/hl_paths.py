import json
import os

HEADLESS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HEADLESS)
BASE = os.path.dirname(REPO)

# config.json: written by INSTALL.bat (the ROMs, Mupen, the decomp's sources); the environment overrides it
try:
    CONFIG = json.load(open(os.path.join(REPO, 'config.json'), encoding='utf-8'))
except (OSError, ValueError):
    CONFIG = {}


def _first(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return paths[-1]


DECOMP = (os.environ.get('SM64_DECOMP') or os.environ.get('T6_DECOMP') or CONFIG.get('decomp')
          or _first(os.path.join(REPO, 'deps', 'sm64-decomp'), os.path.join(BASE, 'sm64-decomp')))
ROM = os.environ.get('SM64_ROM') or CONFIG.get('rom_us') or os.path.join(REPO, 'Super Mario 64 (USA).z64')
MUPEN = os.environ.get('MUPEN') or os.environ.get('MUPEN_EXE') or CONFIG.get('mupen') or 'mupen64.exe'
LAB = os.environ.get('TAS_LAB') or BASE
DATA = os.environ.get('TAS_DATA') or os.path.join(LAB, 'data')


def lab(*parts):
    return os.path.join(LAB, *parts)
