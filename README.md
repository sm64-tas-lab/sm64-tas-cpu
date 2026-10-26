# sm64-tas-cpu: Super Mario 64 headless, for TASers and AIs

The decompiled game compiled natively and driven from Python: **no emulator, no rendering, 35,000 to 55,000
frames/s per core**, in-memory savestates in 0.5 ms, **bit-exact with Mupen64** (checked over thousands of frames
against reference savestates: timer, position, speed, action, angle, RNG and camera identical to the bit).

It ships with an **MCP server**, so an AI (Claude Code, Gemini CLI, any MCP client) can play the game directly:
read Mario and every object from RAM, walk, use the engine's moves (dive rollout, long jumps, slide kick, triple jump,
wall kick, BLJ...), try a move at dozens of facings in a fraction of a second, keep checkpoints, and export its run as
a `.m64` to replay in Mupen.

## Install (Windows, 2 minutes)

You need **Windows 10/11 x64**, **Python 3.9+ 64-bit** (https://www.python.org/downloads/, tick "Add python.exe to
PATH") and **your own Super Mario 64 (USA) ROM** (the JP ROM is optional).

```
git clone https://github.com/sm64-tas-lab/sm64-tas-cpu
cd sm64-tas-cpu
INSTALL.bat
```

`INSTALL.bat` installs numpy and lz4, asks for your ROM, unlocks the game DLLs with it, fetches the decompilation's
sources (code headers only, from n64decomp/sm64), asks for Mupen64-rr-lua (optional), and registers the
`sm64-headless` MCP server for Claude Code (with its skill) and Gemini CLI; `mcp.json` is written for any other client.
Check it with `python agent/sm64_headless_mcp.py --demo`, then ask your AI something like *"start Super Mario 64 with
sm64-headless, scan around Mario and long jump toward the castle"*.

**Why the DLLs are locked:** they are the game itself compiled, so they hold Nintendo's data and are never shipped in
clear. The repo carries `*.dll.locked` files (the way Wafel ships its libsm64 builds): they are encrypted with a key
derived from the original ROM, so only your own dump opens them (`headless/rom_lock.py`). A wrong or modified ROM is
refused with a clear message. No ROM, savestate or game asset is in this repository.

## The AI tools (MCP server `sm64-headless`)

| Tool | What it does |
|---|---|
| `start` | open a game from a Mupen `.st`, a `.m64` / `.hls` from power-on, or power-on itself (US / JP) |
| `state`, `scan` | Mario's state; objects by behaviour name, floors by height, walls, straight from RAM |
| `step`, `walk` | raw inputs; the stick toward a point or a world direction, camera handled |
| `move`, `moves` | the engine's moves; a move the game refuses comes back with a warning |
| `try`, `best` | play and take back; one move at many facings, the best kept |
| `goto`, `collect` | travel (running or chained long jumps, enemies avoided); take an object, verified by the counters |
| `checkpoint`, `restore`, `undo`, `snapshot` | branch and come back |
| `export` | the run as a `.m64` (with its `.st`) for Mupen |

The same game driven live in the real emulator is in **sm64-mupen-bridge** (MCP server `sm64`); its `INSTALL.bat`
can install this repo beside it.

## Questions, problems, feedback

Open an issue on this repository with the command's output and which ROM / step.

## Mupen64-rr-lua (to check movies)

Enable **float exception emulation** (`core.float_exception_emulation = 1` in
`%LOCALAPPDATA%\mupen64-rr-lua\config.json`). Without it, a movie can work on the emulator and **crash on a real N64**.

## Quick start (Python)

```python
import sys; sys.path.insert(0, 'headless')
from sm64hl import Game, write_m64
g = Game(version='us')                         # or 'jp'; from power-on
for _ in range(600):
    g.step((0, 0, 0, 0))                       # 1 input = 1 frame: (stick_x, stick_y, buttons_hi, buttons_lo)
s = g.save(); g.load(s)                        # in-memory savestate
print(g.mario()['action'], g.coins, g.stars)
write_m64(g.history, 'out/run.m64', poweron=True)
```

- Mario state: `g.mario()` gives pos_x/y/z, vel_*, forward_vel, face_yaw, action, level, area, timer, rng, cam_yaw.
- Memory: `g.read(sym|addr, fmt, off)`, `g.write(...)`, `g.addr(sym)` (about 9,000 symbols).
- Objects and geometry: `g.objects()` (behaviour names resolved), `g.surfaces()`.
- A Mupen savestate: `import st_load; st_load.load_st(g, 'my.st')`.
- Movies: `g.load_file('x.m64')` replays a movie from power-on.
- Multi-core parallel search: `parallel_map(fn, candidates, prefix=inputs)`.
- CLI: `python headless/hl.py run|check|m64|bench` (see `headless/README.md`).

## Golden rules

1. `.m64` **inputs** = `(x, y, hi, lo)`; `hi`: A=0x80, B=0x40, Z=0x20, Start=0x10; `lo`: C / L / R.
   Row *n* of a trace = state after *n-1* inputs.
2. A final movie is validated by replaying it in Mupen (with float exception emulation) before announcing a gain.
3. Teleporting (writing the position) is for **exploring** an idea, never a proof.
4. Guards on every search loop (maximum attempts, stop when there is no gain).

## Contents

| Path | Role |
|---|---|
| `agent/` | the MCP server for AIs (`sm64_headless_mcp.py`) and its Claude Code skill |
| `headless/` | the engine (`sm64hl.py`), CLI `hl.py`, process pool, Mupen `.st` reader, ROM lock (`rom_lock.py`) |
| `tas_lib/` | Python helpers (`hlx.py`: objects, closed-loop stick, long-jump chains, collect helpers) |
| `sm64_us.dll.locked`, `sm64_jp.dll.locked` | the game compiled natively, opened by INSTALL.bat with your ROM |
| `install.py`, `INSTALL.bat` | the installer |
