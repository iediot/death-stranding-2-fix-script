# Death Stranding 2: On The Beach — macOS Fix

Patch DS2 so it launches and runs on Apple Silicon Macs under CrossOver with D3DMetal.

Fork of [davidakh/DS2-Mac](https://github.com/davidakh/DS2-Mac), extended to support game
build **v1.10.89.0**.

---

## Quick start

```bash
# 1. Preview (writes nothing)
python3 "Patcher/Version 2/ds2.py" --dry-run "/path/to/DS2.exe"

# 2. Apply
python3 "Patcher/Version 2/ds2.py" "/path/to/DS2.exe"

# 3. Launch without opening the CrossOver GUI
/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle YOUR_BOTTLE "C:\Program Files (x86)\DEATH STRANDING 2 ON THE BEACH\DS2.exe"
```

Undo at any time with `--restore`. A backup is made automatically on first patch.

---

## The problem

DS2 aborts on launch with:

```
Error initializing rendering configuration, check video card and drivers
```

During renderer init, the Decima engine queries two D3D12/DXGI capabilities and treats
both as hard requirements:

| Capability | What D3DMetal reports | Engine behaviour |
|---|---|---|
| `DXGI_FEATURE_PRESENT_ALLOW_TEARING` | `0` (macOS handles vsync natively) | fatal, no fallback |
| `D3D12_FEATURE_DATA_D3D12_OPTIONS2.DepthBoundsTestSupported` | `FALSE` | fatal, no fallback |

Horizon Zero Dawn Remastered uses the same engine and runs fine on D3DMetal because it has
fallback paths for both. DS2 does not — if either check fails, rendering init is torn down
and the game exits.

Neither capability is actually needed to render. Tearing support only matters for variable
refresh rate and low-latency present modes; depth bounds test is a minor deferred-lighting
optimization. The patch makes the engine stop treating their absence as fatal.

A third, **optional** patch addresses HDR: D3DMetal reports an SDR color space to DS2 even
on HDR-capable displays.

---

## Supported builds

| Game build | Patcher | Notes |
|---|---|---|
| v1.0.49.0 | `Patcher/Version 1/ds2.py` | original patterns |
| v1.10.89.0 | `Patcher/Version 2/ds2.py` | re-derived for this fork |

**Use Version 2.** It handles *both* builds — it reads the `FileVersion` string from the
PE version resource, selects the matching pattern set, and falls back to byte-signature
detection if the resource is unreadable. Version 1 is kept only for reference.

Reference binary for v1.10.89.0:

```
size    117,851,944 bytes
sha256  bf3d1c665545930bc850d8f5df486f7395885bb729d4fd408fdb03390de0765b
```

The SHA256 is **informational only**. A mismatch prints a note and patching continues,
because patterns are located by search, not by hash.

---

## How it works

Both required checks live in one function — the renderer init routine at
`0x1420CF6D0 .. 0x1420D03AE` (image base `0x140000000`). Both failure branches converge on
the same block:

```asm
0x1420CFFF9   call 0x1420D2D70    ; release/teardown loop over created COM objects
0x1420CFFFE   xor  dil, dil       ; return value = false
0x1420D0001   jmp  0x1420D0362    ; -> epilogue: movzx eax, dil ; ret
```

`r15` is zeroed at `0x1420CF78E` and never reassigned in the function, so every
`cmp ..., r15d` below is a comparison against zero.

### Patch 1 — ALLOW_TEARING

`IDXGIFactory5::CheckFeatureSupport` is vtable index 28 (`+0xE0`):

```asm
0x1420CFF26   lea  r8, [rsp+0x3C]              ; &allow_tearing
0x1420CFF2B   mov  dword ptr [rsp+0x3C], r15d  ; pre-zero the output
0x1420CFF30   mov  r9d, 4                      ; sizeof(BOOL)
0x1420CFF36   xor  edx, edx                    ; DXGI_FEATURE_PRESENT_ALLOW_TEARING = 0
0x1420CFF3B   call qword ptr [rax+0xE0]
              ...                              ; HRESULT failure logging
0x1420CFF85   cmp  dword ptr [rsp+0x3C], r15d  ; allow_tearing == 0 ?
0x1420CFF8A   je   0x1420CFFF9                 ; unsupported -> FATAL
```

The `je` is replaced with two `nop`s, so execution falls through to the next check.

### Patch 2 — OPTIONS2 DepthBoundsTestSupported

`ID3D12Device::CheckFeatureSupport` is vtable index 13 (`+0x68`):

```asm
0x1420CFF93   lea  r8, [rsp+0x40]              ; &options2
0x1420CFF98   mov  qword ptr [rsp+0x40], r15   ; pre-zero both struct fields
0x1420CFF9D   mov  r9d, 8                      ; sizeof(D3D12_FEATURE_DATA_D3D12_OPTIONS2)
0x1420CFFA3   mov  edx, 0x12                   ; D3D12_FEATURE_D3D12_OPTIONS2 = 18
0x1420CFFAB   call qword ptr [rax+0x68]
              ...                              ; HRESULT failure logging
0x1420CFFF2   cmp  dword ptr [rsp+0x40], r15d  ; DepthBoundsTestSupported == 0 ?
0x1420CFFF7   jne  0x1420D0006                 ; supported -> continue
              (falls through to FATAL)
```

The guarding `jne` becomes an unconditional `jmp`, skipping the failure block entirely.

### What is deliberately *not* patched

There is a **second** `D3D12_OPTIONS2` query in the same function, at `0x1420CFD44`:

```asm
0x1420CFD8B   cmp   dword ptr [rsp+0x58], r15d
0x1420CFD90   ...
0x1420CFDA2   setne byte ptr [rip+0x43111D0]   ; -> global capability flag
```

It writes the result into a global flag instead of branching to the failure block — it is
already a graceful fallback. Leaving it untouched keeps the engine on its non-depth-bounds
code path, which is the correct behaviour. Patching it would falsely advertise depth bounds
support to the renderer.

### Optional — force HDR

At `0x140CF958C .. 0x140CF9DB5` the engine builds a table of DXGI color space descriptors
on the stack, 24 bytes per entry:

```c
struct { const char *name; bool is_hdr; uint32_t value; };
```

The patch flips `is_hdr` from `0` to `1` on the entry for value `1`
(`DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709`, i.e. scRGB linear — a genuinely HDR-capable
color space) so the game stops treating the display as SDR.

Only enable this if your display actually supports HDR.

---

## Patterns (v1.10.89.0)

| Patch | Search | Replace | File offset |
|---|---|---|---|
| ALLOW_TEARING | `44397C243C746D` | `44397C243C9090` | `0x20CF385` |
| OPTIONS2 DepthBounds | `44397C2440750D` | `44397C2440EB0D` | `0x20CF3F2` |
| HDR *(optional)* | `C645E000C745E801000000` | `C645E001C745E801000000` | `0xCF8AF5` |

All three are unique across the full 117 MB image. The patcher verifies uniqueness and
**refuses to patch** if a pattern matches more than once, rather than guessing at the first
hit. Offsets are informational.

### Why the old pattern broke

Only ALLOW_TEARING changed between builds. The compiler switched the fatal branch from a
near jump to a short jump, so the old 11-byte pattern no longer matched:

```
v1.0.49.0    44 39 7C 24 3C   0F 84 F8 00 00 00     je rel32   (11 bytes)
v1.10.89.0   44 39 7C 24 3C   74 6D                 je rel8    (7 bytes)
```

The OPTIONS2 pattern is byte-identical across both builds. Because the upstream tool
aborts if *any* required pattern misses, the whole run failed even though one of the two
patches was still fine.

---

## Usage

```bash
python3 ds2.py [path/to/DS2.exe]              # patch
python3 ds2.py --dry-run [path/to/DS2.exe]    # report only, write nothing
python3 ds2.py --restore [path/to/DS2.exe]    # restore from backup
```

If no path is given, common Steam locations are searched. **Pass the path explicitly** if
your install is anywhere else — auto-detect will not find custom bottle or folder names.

Behaviour:

- `DS2.exe.backup` is created next to the exe before the first write. If one already
  exists it is left alone, never overwritten.
- Nothing is written to disk until all required patches have been resolved, so a failure
  mid-run leaves the exe untouched.
- Re-running on an already-patched exe is safe — it reports `Already patched` and exits
  without writing.
- You are prompted once about the optional HDR patch. Answer `n` unless your display is
  HDR; it is unrelated to booting the game, so leave it off for a first test.

Python 3 standard library only — no `pip install` needed.

---

## Launching

Right-click → Open With → CrossOver works, but opens the CrossOver GUI. To launch the game
directly:

```bash
/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxstart \
  --bottle YOUR_BOTTLE "C:\Program Files (x86)\DEATH STRANDING 2 ON THE BEACH\DS2.exe"
```

Useful flags:

| Flag | Effect |
|---|---|
| `--no-wait` | return immediately instead of blocking until exit |
| `--wait-children` | block until the game and all child processes exit |
| `--workdir "C:\...\DEATH STRANDING 2 ON THE BEACH"` | set working directory — add this if DLL loading fails |

In Automator/Shortcuts, always use the absolute path to `cxstart`; those environments do
not inherit your shell `PATH`.

---

## Verifying

A correct patch changes exactly **3 bytes** and leaves the file size unchanged.

```bash
cmp -l DS2.exe.backup DS2.exe | wc -l      # -> 3  (4 if HDR was also applied)

xxd -s 0x20CF385 -l 7 DS2.exe              # -> 4439 7c24 3c90 90
xxd -s 0x20CF3F2 -l 7 DS2.exe              # -> 4439 7c24 40eb 0d
```

| Offset | Before | After | Meaning |
|---|---|---|---|
| `0x20CF38A` | `74` | `90` | ALLOW_TEARING: `je` → `nop` |
| `0x20CF38B` | `6D` | `90` | ALLOW_TEARING: `je` → `nop` |
| `0x20CF3F7` | `75` | `EB` | OPTIONS2: `jne` → `jmp` |

A 4th difference at `0xCF8AF8` (`00` → `01`) means the optional HDR patch was applied too.

---

## Environment

Built and byte-verified against:

| | |
|---|---|
| Mac | MacBook Pro `Mac17,9` — Apple **M5 Pro** (5P + 10E), 24 GB |
| macOS | **27.0** (build 26A428, Darwin 27.0.0) |
| CrossOver | **26.3.0.39832** |
| Bottle | `win10_64` template, `CX_GRAPHICS_BACKEND=d3dmetal` |
| Game | DS2 **v1.10.89.0** |

Other bottle variables in use: `D3DM_ENABLE_METALFX=0`, `DXMT_ENABLE_NVEXT=0`,
`ROSETTA_ADVERTISE_AVX=1`.

Upstream's v1.0.49.0 work was done on an M2 Max Mac Studio with CrossOver 26 / D3DMetal 3.0.

### Requirements

- Apple Silicon Mac (M1 or newer)
- macOS 26 (Tahoe) or 27
- CrossOver 26.x with D3DMetal enabled as the graphics backend
- DS2 v1.0.49.0 or v1.10.89.0 — other builds need re-derived patterns
- Python 3

> Earlier upstream docs listed `D3DM_SUPPORT_DXR=1` as required. It is **not** set in the
> bottle this fork was built against, and the game launches without it. Treat it as
> optional and only worth trying if you hit ray-tracing-specific problems.

---

## Known issues and limitations

- **Water does not render** — D3DMetal shader translation bug, not something this patch
  can address.
- **Low framerate at first** (~15 FPS reported upstream), improving as the shader cache
  builds.
- **The optional HDR prompt uses `input()` unconditionally.** In a non-interactive context
  — piped stdin, CI, or a shell that does not allocate a TTY — it raises `EOFError` and
  aborts. Nothing is written when this happens, since the file write comes after the
  prompt. Run the patcher from a normal terminal.
- **Authenticode signature and PE checksum are invalidated** by patching. Neither is
  enforced for user-mode executables under Wine/CrossOver, so this does not prevent launch.
- **Game updates will revert the patch** and may shift the patterns again. Re-run the
  patcher after any update; if the patterns no longer match, see below.
- The patch may break if Nixxes ships further updates that restructure renderer init.

---

## Troubleshooting

**`Pattern not found!`** — your build is neither v1.0.49.0 nor v1.10.89.0, or the exe is
already modified by another tool. Check the reported `FileVersion` in the output.

**`Pattern is not unique!`** — the patcher found the same byte sequence twice and stopped
rather than patching blindly. Re-derivation is needed; do not force it.

**`EOFError` at the HDR prompt** — you are running non-interactively. Use a real terminal.

**Game still shows the rendering-configuration error** — verify the patch actually landed
using the `xxd` commands above. If the bytes are correct, the failure is elsewhere: confirm
`CX_GRAPHICS_BACKEND=d3dmetal` is set on the bottle.

**Restore and start over:**

```bash
python3 ds2.py --restore "/path/to/DS2.exe"
```

---

## Re-deriving patterns for a future build

The patterns were found with static analysis, not a brute-force byte search, so the method
is repeatable when the next update shifts everything again.

Tooling: `pip install capstone pefile`.

1. **Anchor on the assertion strings.** Decima's check macros embed the literal source
   expression, so the `.rdata` section contains strings like
   `mDXGIFactory->CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING, &allow_tearing, sizeof(allow_tearing))`.
   Search for `CheckFeatureSupport` to find the cluster.
2. **Find code references.** Scan `.text` for RIP-relative `LEA` (`REX + 8D`, modrm
   mod=00 rm=101) whose resolved target is one of those string addresses. Each anchor has
   exactly one xref.
3. **Get exact function bounds.** Parse the `.pdata` exception directory — an array of
   12-byte `RUNTIME_FUNCTION { BeginAddress, EndAddress, UnwindInfoAddress }` RVAs — and
   binary-search for the entry containing the xref. This matters: linear disassembly from
   a guessed offset desynchronizes on x86.
4. **Disassemble the function** with capstone and locate the `CheckFeatureSupport` calls by
   vtable offset — `+0xE0` for `IDXGIFactory5`, `+0x68` for `ID3D12Device` — cross-checked
   against the feature enum in `edx` (`0` for ALLOW_TEARING, `0x12` for OPTIONS2).
5. **Identify the guard.** Find the `cmp dword ptr [rsp+disp], r15d` after the call and the
   conditional jump following it. Confirm the jump target reaches the teardown block
   (`xor dil, dil`), and confirm `r15` is genuinely zero at that point.
6. **Verify uniqueness** of the candidate byte pattern across the whole image before
   committing to it.

---

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
