<div align="center">

***DS2 Mac Fix***

**Death Stranding 2: On The Beach, running on Apple Silicon through CrossOver**

![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-sqircle&logo=python&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-000000?style=flat-sqircle&logo=apple&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-sqircle)

</div>

---

## Why

DS2 refuses to start under CrossOver. Not a crash, not a black screen — the renderer
declines to initialize at all:

```
Error initializing rendering configuration, check video card and drivers
```

During init, the Decima engine asks the driver for two capabilities and treats both
answers as pass-or-fail. Apple's D3DMetal translation layer says no to both:

| Capability | D3DMetal reports | Why it says no |
|---|---|---|
| `DXGI_FEATURE_PRESENT_ALLOW_TEARING` | `0` | macOS handles vsync itself; there is no tearing present mode to expose |
| `D3D12_FEATURE_DATA_D3D12_OPTIONS2.DepthBoundsTestSupported` | `FALSE` | no Metal equivalent |

Horizon Zero Dawn Remastered runs on the same engine and works fine, because it has
fallback paths for both. DS2 has none — either check failing tears down the renderer and
exits.

Neither capability is needed to draw a frame. Tearing only matters for variable refresh
rate and low-latency present modes; depth bounds test is a deferred-lighting optimization
the engine already knows how to live without. This patch stops the engine treating their
absence as fatal — it does **not** lie to it about what the GPU can do.

## What it patches

Three bytes. Two conditional jumps in the renderer init routine.

| Patch | Instruction change | Required |
|---|---|---|
| **ALLOW_TEARING** | `je <fatal>` → `nop nop` | yes |
| **OPTIONS2 DepthBounds** | `jne <continue>` → `jmp <continue>` | yes |
| **Force HDR** | colorspace table `is_hdr` flag `0` → `1` | optional |

The optional HDR patch exists because D3DMetal reports an SDR color space to DS2 even on
HDR-capable displays. Skip it unless your display is actually HDR.

## Supported builds

| Game build | Patcher | Notes |
|---|---|---|
| v1.0.49.0 | `Patcher/Version 1/ds2.py` | original patterns |
| v1.10.89.0 | `Patcher/Version 2/ds2.py` | re-derived |

**Use Version 2.** It handles both builds — it reads `FileVersion` from the PE version
resource, picks the matching pattern set, and falls back to byte-signature detection if
the resource is unreadable. Version 1 is kept for reference.

## Usage

```bash
python3 ds2.py --dry-run "/path/to/DS2.exe"    # report only, writes nothing
python3 ds2.py "/path/to/DS2.exe"              # patch
python3 ds2.py --restore "/path/to/DS2.exe"    # undo
```

Standard library only — nothing to install.

Pass the path explicitly unless your install is in a default Steam location. `DS2.exe.backup`
is written before the first change and never overwritten. Nothing is committed to disk until
every required patch resolves, so a failed run leaves the executable untouched. Re-running on
an already-patched exe reports `Already patched` and exits.

You are prompted once about HDR. Answer `n` unless you mean it.

## How it works

Both checks live in the renderer init function at `0x1420CF6D0..0x1420D03AE`, and both
failure branches converge on one block:

```asm
0x1420CFFF9   call 0x1420D2D70    ; release the COM objects created so far
0x1420CFFFE   xor  dil, dil       ; return false
0x1420D0001   jmp  0x1420D0362    ; -> epilogue: movzx eax, dil ; ret
```

`r15` is zeroed at `0x1420CF78E` and never written again, so every `cmp ..., r15d` below is
a comparison against zero.

**ALLOW_TEARING** — `IDXGIFactory5::CheckFeatureSupport`, vtable index 28:

```asm
lea  r8, [rsp+0x3C]              ; &allow_tearing
mov  dword ptr [rsp+0x3C], r15d  ; pre-zero the output
mov  r9d, 4                      ; sizeof(BOOL)
xor  edx, edx                    ; DXGI_FEATURE_PRESENT_ALLOW_TEARING = 0
call qword ptr [rax+0xE0]
cmp  dword ptr [rsp+0x3C], r15d  ; unsupported?
je   0x1420CFFF9                 ; -> fatal          << patched to nop nop
```

**OPTIONS2** — `ID3D12Device::CheckFeatureSupport`, vtable index 13:

```asm
lea  r8, [rsp+0x40]              ; &options2
mov  qword ptr [rsp+0x40], r15   ; pre-zero both fields
mov  r9d, 8                      ; sizeof(D3D12_FEATURE_DATA_D3D12_OPTIONS2)
mov  edx, 0x12                   ; D3D12_FEATURE_D3D12_OPTIONS2 = 18
call qword ptr [rax+0x68]
cmp  dword ptr [rsp+0x40], r15d  ; DepthBoundsTestSupported == 0?
jne  0x1420D0006                 ; supported -> continue   << patched to jmp
                                 ; falls through to fatal
```

### What is deliberately left alone

There is a **second** `D3D12_OPTIONS2` query in the same function, at `0x1420CFD44`. It is
not patched, and that is the point:

```asm
cmp   dword ptr [rsp+0x58], r15d
setne byte ptr [rip+0x43111D0]   ; -> global capability flag
```

It records the real answer in a global flag instead of branching to the failure block — it
is already a graceful fallback. Leaving it untouched means the engine still knows depth
bounds is unavailable and stays on its fallback lighting path. Patching it would advertise
hardware support that isn't there, which is how you get corruption rather than a fix.

The same logic applies to tearing: the variable still reads `0` afterwards. Only the abort
is removed.

### Patterns (v1.10.89.0)

| Patch | Search | Replace | Offset |
|---|---|---|---|
| ALLOW_TEARING | `44397C243C746D` | `44397C243C9090` | `0x20CF385` |
| OPTIONS2 | `44397C2440750D` | `44397C2440EB0D` | `0x20CF3F2` |
| HDR | `C645E000C745E801000000` | `C645E001C745E801000000` | `0xCF8AF5` |

All three are unique across the full 117 MB image. The patcher checks uniqueness and
**refuses to patch an ambiguous match** rather than taking the first hit.

### Why the old pattern broke

Only ALLOW_TEARING changed between builds. The compiler moved the fatal branch from a near
jump to a short one:

```
v1.0.49.0    44 39 7C 24 3C   0F 84 F8 00 00 00     je rel32
v1.10.89.0   44 39 7C 24 3C   74 6D                 je rel8
```

OPTIONS2 is byte-identical in both. Because the upstream tool aborts when any required
pattern misses, the whole run failed over one of two patches.

## Launching

Right-click → Open With → CrossOver opens the GUI first. To go straight to the game:

```bash
/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle YOUR_BOTTLE "C:\Program Files (x86)\DEATH STRANDING 2 ON THE BEACH\DS2.exe"
```

| Flag | Effect |
|---|---|
| `--no-wait` | return immediately instead of blocking |
| `--wait-children` | block until the game and its children exit |
| `--workdir "C:\...\DEATH STRANDING 2 ON THE BEACH"` | set working directory if DLL loading fails |
| `--desktop name,1280x800` | run in a fixed-size virtual desktop |

In Automator or Shortcuts, use the absolute path — those environments don't inherit your
shell `PATH`.

## Performance on Apple Silicon

Getting it to boot is one problem. Getting it to run is another, and the fixes are not in
the game's menu.

**Turn off CrossOver's High Resolution / Retina mode.** This is the single biggest factor,
by a wide margin. With Retina mode on and `LogPixels` at 192 DPI, a window you set to
1920×1200 is backed by 3840×2400 real pixels — **4× the pixel count you selected**. Every
graphics setting in the game is rounding error next to that. It is also why the resolution
dropdown won't offer anything below 1920.

**Enable MSync** in the bottle settings. Wine's synchronization primitives are worth a large
multiple in threaded games.

**Cap the framerate with VSync.** DS2's VSync dropdown offers refresh divisors, and the
refresh-rate selector is often locked, so this is your cap:

| VSync | Present interval | On a 120 Hz panel |
|---|---|---|
| On | every refresh | 120 fps |
| Half | every 2nd | 60 fps |
| Third | every 3rd | 40 fps |

Half is usually right. Capping cuts sustained power draw, which matters because thermal
throttling on a laptop chassis will take back whatever tuning gained you.

**Use FSR, not DLSS.** If DLSS appears in the upscaler list, it is a detection artifact —
CrossOver's DLSS option and `sl.interposer.dll` convince the game NVIDIA features exist.
DLSS runs on Tensor cores, which Apple Silicon does not have, and there is no software
fallback. FSR is vendor-agnostic compute and actually runs. The same applies to frame
generation: the FidelityFX one can work, the NVIDIA one cannot.

**Give the shader cache time.** D3DMetal's translated-pipeline cache is per-bottle, so it
starts empty in a new bottle and after settings changes invalidate variants. The first
fifteen minutes in any area are not representative. Change one thing at a time and let it
settle before judging.

**Use a dedicated bottle.** Don't share one across DirectX eras — a DX9-era profile and a
DX12 title want different configurations, and CrossOver's per-app install profiles will
fight each other. Bottle overhead is 1–3 GB against a 113 GB game, so the disk cost is
noise.

## Verifying

Three bytes change. File size never does.

```bash
cmp -l DS2.exe.backup DS2.exe | wc -l      # 3, or 4 with HDR

xxd -s 0x20CF385 -l 7 DS2.exe              # 4439 7c24 3c90 90
xxd -s 0x20CF3F2 -l 7 DS2.exe              # 4439 7c24 40eb 0d
```

| Offset | Before | After | Meaning |
|---|---|---|---|
| `0x20CF38A` | `74` | `90` | ALLOW_TEARING `je` → `nop` |
| `0x20CF38B` | `6D` | `90` | ALLOW_TEARING `je` → `nop` |
| `0x20CF3F7` | `75` | `EB` | OPTIONS2 `jne` → `jmp` |

A fourth difference at `0xCF8AF8` means HDR was applied too.

Reference unpatched v1.10.89.0: `117,851,944` bytes,
sha256 `bf3d1c665545930bc850d8f5df486f7395885bb729d4fd408fdb03390de0765b`. The patcher
reports a hash mismatch as a note, not an error — patterns are found by search, not by hash.

## Known issues

| Issue | Cause |
|---|---|
| Water does not render | D3DMetal shader translation bug, not the patch |
| Sprites stretched into the sky | Same layer. Both are alpha/translucency paths — cycling **Translucency Quality** sometimes routes around it |
| "recommend newer drivers (at least 595.79)" | Benign. That is an NVIDIA version string being checked against an adapter reporting as AMD. It gates nothing |
| `Unsupported API: CheckFeatureSupport, unhandled support query 9` | A middleware DLL probing, not DS2 — the engine never queries feature 9. A log line, not an error |
| Low FPS at startup | Shader cache warming |
| Signature and PE checksum invalidated | Unavoidable when patching. Not enforced for user-mode executables under Wine |
| Game updates revert the patch | Re-run the patcher. If patterns no longer match, see below |

## Re-deriving patterns

Patterns were found by static analysis, not a byte hunt, so the method repeats when the next
update moves everything. Needs `capstone` and `pefile`.

1. **Anchor on the assertion strings.** Decima's check macros embed the literal source
   expression, so `.rdata` holds strings like
   `mDXGIFactory->CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING, &allow_tearing, sizeof(allow_tearing))`.
2. **Find the code reference.** Scan `.text` for RIP-relative `LEA` (`REX + 8D`, modrm
   mod=00 rm=101) resolving to those addresses. Each anchor has exactly one.
3. **Get real function bounds** from `.pdata` — an array of 12-byte
   `RUNTIME_FUNCTION { Begin, End, UnwindInfo }` RVAs. Binary-search for the entry containing
   the xref. Skipping this means linear disassembly starts mid-instruction and desynchronizes.
4. **Disassemble and locate the calls** by vtable offset — `+0xE0` on `IDXGIFactory5`,
   `+0x68` on `ID3D12Device` — cross-checked against the feature id in `edx`.
5. **Find the guard**: the `cmp dword ptr [rsp+disp], r15d` after the call and the jump
   following it. Confirm the target reaches the teardown block, and that `r15` is really zero.
6. **Check uniqueness** across the whole image before trusting the pattern.

## Credits

Original fix and patcher by **David** ([davidakh](https://github.com/davidakh)).
v1.10.89.0 patterns re-derived for this fork.

## Disclaimer

This patch modifies a copyrighted executable for personal compatibility purposes. No game
code or assets are distributed. You must own a legitimate copy of Death Stranding 2.

## License

MIT
