# DS2 Mac Fix

**Death Stranding 2: On The Beach, running on Apple Silicon through CrossOver**

---

DS2's Decima engine hard-requires two D3D12 features that Apple's D3DMetal doesn't report,
and bails out of renderer init with no fallback. This patches out the two aborts — three
bytes, nothing else. Game builds **v1.0.49.0** and **v1.10.89.0**, auto-detected.

## 1. Bottle

All three have to be right before the patch is even the problem.

| | Value | Why |
|---|---|---|
| Graphics backend | `D3DMetal` | CrossOver defaults to **DXVK**, which doesn't do DX12 properly here |
| Env var | `ROSETTA_ADVERTISE_AVX=1` | Rosetta hides AVX/F16C CPUID bits from translated x86 apps |
| Game build | `v1.10.89.0`+ | Earlier builds have a CPU-detection bug affecting Nixxes ports generally |

Use a dedicated bottle — don't share one across DirectX eras, CrossOver's per-app profiles
will fight each other.

## 2. Patch

```bash
python3 patcher/ds2.py --dry-run "/path/to/DS2.exe"    # report only, writes nothing
python3 patcher/ds2.py "/path/to/DS2.exe"              # patch
python3 patcher/ds2.py --restore "/path/to/DS2.exe"    # undo
```

Standard library only. Writes `DS2.exe.backup` before the first change and never overwrites
it; nothing is committed unless every required patch resolves. Answer `n` to the HDR prompt
unless your display is genuinely HDR. Verify with `cmp -l DS2.exe.backup DS2.exe | wc -l` →
`3`, or `4` with HDR.

## 3. Launch

From the command line, not Finder. D3DMetal doesn't reliably engage on a double-click or an
app shortcut, and when it doesn't you get the renderer error back with a correctly patched
executable.

```bash
CX_GRAPHICS_BACKEND=d3dmetal D3DM_ENABLE_METALFX=1 DXMT_ENABLE_NVEXT=0 \
/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle YOUR_BOTTLE "C:\Program Files (x86)\DEATH STRANDING 2 ON THE BEACH\DS2.exe"
```

`CX_GRAPHICS_BACKEND` is the one that matters. `D3DM_ENABLE_METALFX` allows MetalFX
upscaling; `DXMT_ENABLE_NVEXT=0` hides the NVIDIA interposer so the game stops offering DLSS
it can't run. Add `--workdir "C:\...\ON THE BEACH"` if DLL loading fails, or
`--desktop name,1280x800` for a fixed-size virtual desktop. In Automator or Shortcuts use the
absolute path — those don't inherit your shell `PATH`.

## If it still crashes

Three different problems print the *same* renderer message. **The adapter name in the crash
log tells them apart:** `VirtualApple` means D3DMetal isn't engaging and the patch is not
your problem.

| Error | What it actually is |
|---|---|
| "VC++ 2015-2022 Redistributable required" | Red herring — the redist is fine. It's what the game shows when early init fails for any reason |
| `Error initializing rendering configuration` | Bottle is on DXVK → step 1 |
| …same message, adapter logged as `VirtualApple` | D3DMetal isn't engaging → step 3 |
| …same message, adapter logged as your real GPU | The real one → step 2 |
| "Shader Model 6.6 not detected. Current GPU: Apple M5 Pro" | Progress — D3DMetal is engaging and seeing the real GPU. Fixed by the game update |
| "requires a CPU that supports F16C instructions" | Rosetta hiding CPUID flags → the env var, then the game update |

Once it runs: water not rendering and sprites stretched into the sky are D3DMetal
translucency-path bugs, not the patch (cycling **Translucency Quality** sometimes routes
around it). The "recommend newer drivers (595.79)" notice and `unhandled support query 9` are
both benign. Low FPS at startup is the shader cache warming. Game updates revert the patch —
just re-run it.

## Performance

| Do | Why |
|---|---|
| **Turn off High Resolution / Retina mode** | By far the biggest factor. At 192 DPI a 1920×1200 window is backed by 3840×2400 real pixels — 4× what you selected. It's also why the resolution dropdown won't go below 1920 |
| **Enable MSync** | Wine's sync primitives are worth a large multiple in threaded games |
| **Cap fps with VSync** | The refresh selector is often locked, so the divisors are your cap: On/Half/Third = 120/60/40 on a 120 Hz panel. Half is usually right — throttling takes back whatever tuning gained you |
| **FSR, not DLSS** | DLSS in the list is a `sl.interposer.dll` artifact. It needs Tensor cores, with no software fallback. Same for frame gen: FidelityFX works, NVIDIA's can't |
| **Give the shader cache time** | Per-bottle and starts empty; settings changes invalidate variants. The first fifteen minutes in an area aren't representative |

## What it changes

| Patch | Change | Search → replace | Offset |
|---|---|---|---|
| ALLOW_TEARING | `je <fatal>` → `nop nop` | `44397C243C746D` → `44397C243C9090` | `0x20CF385` |
| OPTIONS2 DepthBounds | `jne <cont>` → `jmp <cont>` | `44397C2440750D` → `44397C2440EB0D` | `0x20CF3F2` |
| Force HDR *(optional)* | `is_hdr` `0` → `1` | `C645E000C745E8…` → `C645E001C745E8…` | `0xCF8AF5` |

Offsets are for v1.10.89.0; patterns are located by search and checked for uniqueness across
the 117 MB image, and an ambiguous match is refused rather than guessed. Both feature
variables still read `0` afterwards — only the aborts are removed, so the engine stays on its
fallback paths instead of being told hardware exists that doesn't. Neither feature is needed
to draw a frame; Horizon Zero Dawn Remastered runs the same engine and simply has fallbacks
where DS2 has none.

Only ALLOW_TEARING changed between builds — the compiler moved the fatal branch from
`0F 84` (`je rel32`) to `74` (`je rel8`), and since the upstream tool aborts when any required
pattern misses, the whole run failed over one of two patches.

**Full analysis** — addresses, disassembly, the second `OPTIONS2` query that is deliberately
*not* patched, and the capstone/pefile method for re-deriving all of it after the next game
update — is in the module docstring of [`patcher/ds2.py`](patcher/ds2.py).

## Repo

```
patcher/ds2.py                  # use this one — handles both builds
patcher/legacy/ds2-1.0.49.0.py  # upstream v1.0.49.0-only patcher, reference
```

| Release | Change |
|---|---|
| v1.10.89.0 support | Re-derived ALLOW_TEARING pattern; one patcher auto-detects the build; `--dry-run` and `--restore` |
| Upstream v1 | Optional force-HDR patch |
| Upstream v0 | Fixed DS2 not launching |

Original fix and patcher by **David** ([davidakh](https://github.com/davidakh)); v1.10.89.0
patterns re-derived for this fork. MIT.

Modifies a copyrighted executable for personal compatibility only — no game code or assets
are distributed, and you must own a legitimate copy of Death Stranding 2.
