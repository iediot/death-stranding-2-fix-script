# Death Stranding 2: On The Beach — macOS Fix

Run Death Stranding 2 on Apple Silicon Macs via CrossOver/D3DMetal.

Fork of [davidakh/DS2-Mac](https://github.com/davidakh/DS2-Mac), adding support for
game build **v1.10.89.0**.

## The Problem

DS2 crashes on launch with `"Error initializing rendering configuration"` when running
through CrossOver with D3DMetal. The game's Decima engine queries two D3D12/DXGI
capabilities during renderer init and treats both as hard requirements:

1. **`DXGI_FEATURE_PRESENT_ALLOW_TEARING`** — D3DMetal returns `0` (macOS handles vsync natively)
2. **`D3D12_FEATURE_DATA_D3D12_OPTIONS2.DepthBoundsTestSupported`** — D3DMetal returns `FALSE`

Unlike Horizon Zero Dawn Remastered (same engine, works fine on D3DMetal), DS2 has no
fallback path — if either check fails, rendering init is aborted entirely.

Neither feature is actually needed to render. Tearing support is only used for variable
refresh rate / low-latency present modes, and depth bounds test is a minor deferred
lighting optimization.

There is also an optional third patch for HDR: D3DMetal reports an SDR color space to DS2
even on HDR-capable displays.

## Supported builds

| Game build | Patcher | Status |
|---|---|---|
| v1.0.49.0  | `Patcher/Version 1/ds2.py` | original patterns |
| v1.10.89.0 | `Patcher/Version 2/ds2.py` | re-derived |

**Version 2 handles both builds.** It detects which one you have from the PE version
resource and applies the matching pattern set, so it's the only one you need.

## What changed in v1.10.89.0

Only the ALLOW_TEARING pattern broke. The compiler switched the fatal branch from a near
jump to a short jump, so the old 11-byte pattern no longer matches:

```
v1.0.49.0    44 39 7C 24 3C   0F 84 F8 00 00 00     je rel32
v1.10.89.0   44 39 7C 24 3C   74 6D                 je rel8
```

The OPTIONS2 DepthBounds pattern is byte-identical across both builds.

### Patterns (v1.10.89.0)

| Check | Search | Replace | Offset |
|---|---|---|---|
| ALLOW_TEARING | `44397C243C746D` | `44397C243C9090` | `0x20CF385` |
| OPTIONS2 DepthBounds | `44397C2440750D` | `44397C2440EB0D` | `0x20CF3F2` |
| HDR (optional) | `C645E000C745E801000000` | `C645E001C745E801000000` | `0xCF8AF5` |

Offsets are informational — the patcher locates patterns by search, and refuses to patch
if a pattern matches more than once.

Both required checks live in the renderer init function at `0x1420CF6D0..0x1420D03AE`
and converge on the same failure block:

```
0x1420CFFF9   call 0x1420D2D70    ; release/teardown of created COM objects
0x1420CFFFE   xor  dil, dil       ; return false
0x1420D0001   jmp  0x1420D0362    ; -> epilogue, movzx eax, dil
```

Patch 1 NOPs the `je` into that block; patch 2 turns the guarding `jne` into an
unconditional `jmp` that skips it.

Note there is a *second* `D3D12_OPTIONS2` query in the same function (at `0x1420CFD44`).
It is deliberately **not** patched: it feeds `setne` into a global capability flag rather
than branching to the failure block, so it is already a graceful fallback. Leaving it
alone keeps the engine on its non-depth-bounds code path.

## Usage

```bash
# Preview without writing anything
python3 ds2.py --dry-run "/path/to/DS2.exe"

# Apply the patch
python3 ds2.py "/path/to/DS2.exe"

# Restore original
python3 ds2.py --restore "/path/to/DS2.exe"
```

The typical path inside a CrossOver bottle is:

```
~/Library/Application Support/CrossOver/Bottles/<bottle>/drive_c/
Program Files (x86)/Steam/steamapps/common/
DEATH STRANDING 2 - ON THE BEACH/DS2.exe
```

If your install is elsewhere, pass the path explicitly — auto-detect only covers the
common Steam locations.

A backup (`DS2.exe.backup`) is created automatically before patching. Re-running the
patcher on an already-patched exe is safe; it reports `Already patched` and writes nothing.

You will be prompted once about the optional HDR patch. Answer `n` unless your display is
actually HDR — it is unrelated to getting the game to boot, so leave it off for a first test.

### Verifying

A correct patch changes exactly **3 bytes** and leaves the file size unchanged.
Count the differences against the backup:

```bash
cmp -l DS2.exe.backup DS2.exe | wc -l      # -> 3  (4 if HDR was also applied)
```

Then check the patched bytes directly:

```bash
xxd -s 0x20CF385 -l 7 DS2.exe              # -> 4439 7c24 3c90 90
xxd -s 0x20CF3F2 -l 7 DS2.exe              # -> 4439 7c24 40eb 0d
```

Which corresponds to:

| Offset | Before | After | Meaning |
|---|---|---|---|
| `0x20CF38A` | `74` | `90` | ALLOW_TEARING: `je` -> `nop` |
| `0x20CF38B` | `6D` | `90` | ALLOW_TEARING: `je` -> `nop` |
| `0x20CF3F7` | `75` | `EB` | OPTIONS2: `jne` -> `jmp` |

A 4th difference at `0xCF8AF8` (`00` -> `01`) means the optional HDR patch was applied too.

## Requirements

- **macOS Tahoe 26.4** on Apple Silicon (M1/M2/M3/M4)
- **CrossOver Preview 20260323** with D3DMetal enabled
- **DS2 v1.0.49.0 or v1.10.89.0** (Steam) — other builds may need updated patterns
- **CrossOver Settings** D3DMetal, MSync, `D3DM_SUPPORT_DXR=1`
- **Python 3** (standard library only — no dependencies)

## Known issues

- Water does not render (D3DMetal shader translation bug)
- ~15 FPS initially, improves as the shader cache builds
- Patching invalidates the exe's Authenticode signature and PE checksum. Neither is
  enforced for user-mode executables under Wine/CrossOver, so this does not block launch.
- The optional HDR prompt uses `input()` unconditionally. In a non-interactive context
  (piped stdin, CI) it raises `EOFError` and aborts before anything is written. Run it
  from a normal terminal.
- Version 2 prints a note if the exe's SHA256 differs from its reference hash. This is
  informational only and does not block patching — patterns are matched by search, not
  by hash.

## Rebuilding the analysis

Patterns were re-derived with static analysis rather than a flat disassembly dump:

- `pefile` for PE headers, sections and the version resource
- RIP-relative `LEA` cross-references to the engine's assertion strings, which embed the
  literal expression text (e.g. `mDXGIFactory->CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING, ...)`)
- the `.pdata` exception directory for exact function bounds, so linear disassembly starts
  correctly aligned
- `capstone` for disassembly and branch-target resolution

## Credits

Original fix and patcher by **David** ([davidakh](https://github.com/davidakh)) —
david.akhmedbayev@icloud.com

v1.10.89.0 patterns re-derived for this fork.

## Disclaimer

This patch modifies a copyrighted executable for personal compatibility purposes. No game
code or assets are distributed. You must own a legitimate copy of Death Stranding 2.
Use at your own risk.

## License

MIT
