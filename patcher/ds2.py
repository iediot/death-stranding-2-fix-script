#!/usr/bin/env python3
"""
Death Stranding 2: On The Beach - macOS/CrossOver Compatibility Patch
=====================================================================

Applies hex patches to DS2.exe to bypass two D3D12 feature checks that
fail on Apple's D3DMetal translation layer, allowing the game to boot
and run via CrossOver on Apple Silicon Macs.

Supported builds:
  - v1.0.49.0   (original patterns, from the upstream DS2-Mac tool)
  - v1.10.89.0  (re-derived for this release)

Root Cause:
  DS2's Decima engine queries two D3D12/DXGI capabilities during renderer
  init and treats both as hard requirements. Unlike HZD Remastered (older
  engine, has fallback paths), DS2 has no graceful fallback and bails out
  with "Error initializing rendering configuration" if either check fails.

  In v1.10.89.0 both checks live in the renderer init function at
  0x1420CF6D0..0x1420D03AE (RVA base 0x140000000). Both conditional jumps
  converge on the same failure block at 0x1420CFFF9:

      0x1420CFFF9  call 0x1420D2D70     ; release/teardown of created COM objects
      0x1420CFFFE  xor  dil, dil        ; return value = false
      0x1420D0001  jmp  0x1420D0362     ; -> epilogue, movzx eax, dil

Required Patches:
  1. DXGI_FEATURE_PRESENT_ALLOW_TEARING check
     IDXGIFactory5::CheckFeatureSupport (vtable +0xE0) at 0x1420CFF3B:

         lea  r8,  [rsp+0x3C]          ; &allow_tearing
         mov  dword ptr [rsp+0x3C], r15d   ; pre-zero (r15 == 0 here)
         mov  r9d, 4                   ; sizeof(BOOL)
         xor  edx, edx                 ; DXGI_FEATURE_PRESENT_ALLOW_TEARING = 0
         call qword ptr [rax+0xE0]
         ...
         cmp  dword ptr [rsp+0x3C], r15d   ; allow_tearing == 0 ?
         je   0x1420CFFF9              ; unsupported -> FATAL

     D3DMetal returns allow_tearing = 0 (macOS handles vsync differently).
     Patch: NOP the JE so init falls through to the next check.

     NOTE: this is the pattern that broke between v1.0.49.0 and v1.10.89.0.
     The compiler switched the fatal branch from a near JE (0F 84 rel32) to
     a short JE (74 rel8), so the old 11-byte pattern no longer matches.

  2. D3D12_FEATURE_DATA_D3D12_OPTIONS2.DepthBoundsTestSupported check
     ID3D12Device::CheckFeatureSupport (vtable +0x68) at 0x1420CFFAB:

         lea  r8,  [rsp+0x40]          ; &options2
         mov  qword ptr [rsp+0x40], r15    ; pre-zero both struct fields
         mov  r9d, 8                   ; sizeof(D3D12_FEATURE_DATA_D3D12_OPTIONS2)
         mov  edx, 0x12                ; D3D12_FEATURE_D3D12_OPTIONS2 = 18
         call qword ptr [rax+0x68]
         ...
         cmp  dword ptr [rsp+0x40], r15d   ; DepthBoundsTestSupported == 0 ?
         jne  0x1420D0006              ; supported -> continue
         (falls through to FATAL)

     D3DMetal returns DepthBoundsTestSupported = FALSE.
     Patch: JNE -> JMP so init always continues.

  Note there is a *second* D3D12_OPTIONS2 query in the same function (at
  0x1420CFD44, the "&option2_features" one). That one is NOT patched: it
  feeds `setne` into a global capability flag rather than branching to the
  failure block, so it is already a graceful fallback. Leaving it alone
  keeps the engine on its non-depth-bounds code path, which is what we want.

Optional Patch:
  3. Force HDR detection
     - D3DMetal reports SDR color space to DS2 despite supporting HDR
       (HZD Remastered correctly detects HDR on the same setup)
     - Sets the HDR flag on the DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709
       (scRGB, value 1) entry of the colorspace descriptor table built at
       0x140CF96F5 (24-byte entries: {const char* name; bool is_hdr; u32 value;})
     - Only apply this if your display supports HDR

Neither required feature is needed for rendering. Tearing support is only
used for variable refresh rate / low-latency present modes; depth bounds
test is a minor optimization for deferred lighting passes.

Re-deriving these patterns after a future game update (needs capstone + pefile):
  1. Anchor on the assertion strings. Decima's check macros embed the literal
     source expression, so .rdata holds strings like
       "mDXGIFactory->CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING,
        &allow_tearing, sizeof(allow_tearing))"
  2. Find the code reference: scan .text for RIP-relative LEA (REX + 8D, modrm
     mod=00 rm=101) resolving to those addresses. Each anchor has exactly one.
  3. Get real function bounds from .pdata - an array of 12-byte
     RUNTIME_FUNCTION { Begin, End, UnwindInfo } RVAs. Binary-search for the
     entry containing the xref. Skipping this means linear disassembly starts
     mid-instruction and desynchronizes.
  4. Disassemble and locate the calls by vtable offset (+0xE0 on IDXGIFactory5,
     +0x68 on ID3D12Device), cross-checked against the feature id in edx.
  5. Find the guard: the `cmp dword ptr [rsp+disp], r15d` after the call and the
     jump following it. Confirm the target reaches the teardown block, and that
     r15 is really zero at that point.
  6. Check uniqueness across the whole image before trusting the pattern.

Usage:
  python3 ds2.py [path_to_DS2.exe]
  python3 ds2.py --dry-run [path_to_DS2.exe]
  python3 ds2.py --restore [path_to_DS2.exe]

If no path is given, it will look in common CrossOver/Steam locations.

Original author: Said (github.com/davidakh)
v1.10.89.0 patterns re-derived via capstone/pefile static analysis.
"""

import sys
import os
import re
import shutil
import hashlib

# ---------------------------------------------------------------------------
# Per-build patch definitions
# ---------------------------------------------------------------------------

BUILDS = {
    "1.10.89.0": {
        "size": 117851944,
        "sha256": "bf3d1c665545930bc850d8f5df486f7395885bb729d4fd408fdb03390de0765b",
        "required": [
            {
                "name": "DXGI_FEATURE_PRESENT_ALLOW_TEARING bypass",
                "desc": "NOP the JE that aborts init when tearing is unsupported",
                # cmp dword ptr [rsp+0x3C], r15d ; je 0x1420CFFF9
                "search":  bytes.fromhex("44397C243C746D"),
                # cmp dword ptr [rsp+0x3C], r15d ; nop ; nop
                "replace": bytes.fromhex("44397C243C9090"),
                "expect_off": 0x20CF385,
            },
            {
                "name": "D3D12_OPTIONS2.DepthBoundsTestSupported bypass",
                "desc": "Change JNE to JMP so init continues without depth bounds test",
                # cmp dword ptr [rsp+0x40], r15d ; jne 0x1420D0006
                "search":  bytes.fromhex("44397C2440750D"),
                # cmp dword ptr [rsp+0x40], r15d ; jmp 0x1420D0006
                "replace": bytes.fromhex("44397C2440EB0D"),
                "expect_off": 0x20CF3F2,
            },
        ],
        "optional": [
            {
                "name": "Force HDR detection",
                "desc": "Mark scRGB (RGB_FULL_G10_NONE_P709) as HDR in the colorspace table",
                "prompt": "Force HDR? D3DMetal reports SDR to DS2 even on HDR displays.\n"
                          "  Only enable this if your display supports HDR. (y/N): ",
                "search":  bytes.fromhex("C645E000C745E801000000"),
                "replace": bytes.fromhex("C645E001C745E801000000"),
                "expect_off": 0xCF8AF5,
            },
        ],
    },
    "1.0.49.0": {
        "size": None,
        "sha256": None,
        "required": [
            {
                "name": "DXGI_FEATURE_PRESENT_ALLOW_TEARING bypass",
                "desc": "NOP the JZ that skips rendering init when tearing is unsupported",
                "search":  bytes.fromhex("44397C243C0F84F8000000"),
                "replace": bytes.fromhex("44397C243C909090909090"),
                "expect_off": None,
            },
            {
                "name": "D3D12_OPTIONS2.DepthBoundsTestSupported bypass",
                "desc": "Change JNZ to JMP so init continues even without depth bounds test",
                "search":  bytes.fromhex("44397C2440750D"),
                "replace": bytes.fromhex("44397C2440EB0D"),
                "expect_off": None,
            },
        ],
        "optional": [
            {
                "name": "Force HDR detection",
                "desc": "Mark SDR color space as HDR in the lookup table",
                "prompt": "Force HDR? D3DMetal reports SDR to DS2 even on HDR displays.\n"
                          "  Only enable this if your display supports HDR. (y/N): ",
                "search":  bytes.fromhex("C645E000C745E801000000"),
                "replace": bytes.fromhex("C645E001C745E801000000"),
                "expect_off": None,
            },
        ],
    },
}

DEFAULT_PATHS = [
    os.path.expanduser(p) for p in (
        "~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/"
        "Program Files (x86)/Steam/steamapps/common/"
        "DEATH STRANDING 2 - ON THE BEACH/DS2.exe",

        "~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/"
        "Program Files (x86)/Steam/steamapps/common/"
        "DEATH STRANDING 2 - ON THE BEACH/ds2.exe",

        "~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/"
        "Program Files (x86)/DEATH STRANDING 2 ON THE BEACH/DS2.exe",
    )
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_exe(user_path=None):
    if user_path:
        if os.path.isfile(user_path):
            return user_path
        print(f"Error: File not found: {user_path}")
        sys.exit(1)

    for p in DEFAULT_PATHS:
        if os.path.isfile(p):
            print(f"Found DS2.exe at: {p}")
            return p

    print("Could not find DS2.exe automatically.")
    print("Please provide the path as an argument:")
    print("  python3 ds2.py /path/to/DS2.exe")
    sys.exit(1)


def read_file_version(data):
    """Best-effort FileVersion string from the PE VS_VERSION_INFO resource."""
    key = b'F\x00i\x00l\x00e\x00V\x00e\x00r\x00s\x00i\x00o\x00n\x00\x00\x00'
    i = data.find(key)
    if i < 0:
        return None
    p = i + len(key)
    while p < len(data) and data[p] == 0:   # skip 32-bit alignment padding
        p += 1
    m = re.match(rb'(?:[\x20-\x7e]\x00)+', data[p:p + 80])
    if not m:
        return None
    return m.group().decode('utf-16le').strip()


def patch_state(data, patch):
    """Return ('unpatched', off) | ('patched', off) | ('missing', None)."""
    off = data.find(patch["search"])
    if off != -1:
        return "unpatched", off
    off = data.find(patch["replace"])
    if off != -1:
        return "patched", off
    return "missing", None


def detect_build(data):
    """Pick a build whose required patterns all resolve (matched or already patched)."""
    version = read_file_version(data)
    if version in BUILDS:
        return version, "FileVersion resource"

    for name, spec in BUILDS.items():
        if all(patch_state(data, p)[0] != "missing" for p in spec["required"]):
            return name, "byte-pattern signature"

    return None, None


def backup(path):
    backup_path = path + ".backup"
    if os.path.exists(backup_path):
        print(f"Backup already exists: {backup_path}")
        return backup_path
    print(f"Creating backup: {backup_path}")
    shutil.copy2(path, backup_path)
    return backup_path


def apply_patch_list(result, data, patches, dry_run=False):
    """Apply patches to `result`. Returns count applied, or -1 on hard failure."""
    applied = 0
    for patch in patches:
        name = patch["name"]
        state, off = patch_state(data, patch)

        if state == "patched":
            print(f"  [{name}] Already patched (offset 0x{off:X})")
            continue

        if state == "missing":
            print(f"  [{name}] ERROR: Pattern not found!")
            print(f"    This exe version may not be supported.")
            print(f"    Expected bytes: {patch['search'].hex()}")
            return -1

        # Refuse to guess when the signature is ambiguous.
        second = data.find(patch["search"], off + 1)
        if second != -1:
            print(f"  [{name}] ERROR: Pattern is not unique!")
            print(f"    First at 0x{off:X}, second at 0x{second:X}")
            print(f"    Refusing to patch an ambiguous match.")
            return -1

        expect = patch.get("expect_off")
        if expect is not None and off != expect:
            print(f"  [{name}] NOTE: found at 0x{off:X}, expected 0x{expect:X}")

        if dry_run:
            print(f"  [{name}] Would patch at offset 0x{off:X}"
                  f"  ({patch['search'].hex()} -> {patch['replace'].hex()})")
        else:
            result[off:off + len(patch["replace"])] = patch["replace"]
            print(f"  [{name}] Patched at offset 0x{off:X}")
        applied += 1

    return applied


def apply_patches(path, dry_run=False):
    with open(path, "rb") as f:
        data = f.read()

    print(f"File size: {len(data):,} bytes")

    version, how = detect_build(data)
    if version is None:
        print("\nERROR: Unrecognised DS2.exe build.")
        detected = read_file_version(data)
        print(f"  FileVersion resource says: {detected or 'unknown'}")
        print(f"  Known builds: {', '.join(BUILDS)}")
        return False

    spec = BUILDS[version]
    print(f"Detected build: v{version}  (via {how})")

    expected_size = spec.get("size")
    if expected_size is not None and len(data) != expected_size:
        print(f"  WARNING: expected {expected_size:,} bytes, got {len(data):,}")

    expected_hash = spec.get("sha256")
    if expected_hash:
        digest = hashlib.sha256(data).hexdigest()
        if digest == expected_hash:
            print("  SHA256 matches known-good unpatched exe.")
        else:
            print(f"  NOTE: SHA256 {digest}")
            print("        differs from the reference unpatched exe")
            print("        (expected if already patched, or a different release).")

    result = bytearray(data)
    total_applied = 0

    print("\nRequired patches (needed to boot):")
    count = apply_patch_list(result, data, spec["required"], dry_run)
    if count == -1:
        return False
    total_applied += count

    print("\nOptional patches:")
    for patch in spec["optional"]:
        name = patch["name"]
        state, off = patch_state(data, patch)

        if state == "patched":
            print(f"  [{name}] Already patched")
            continue
        if state == "missing":
            print(f"  [{name}] Pattern not found (version mismatch?)")
            continue
        if data.find(patch["search"], off + 1) != -1:
            print(f"  [{name}] Pattern not unique - skipping")
            continue

        if dry_run:
            print(f"  [{name}] Available at offset 0x{off:X} (not prompted in --dry-run)")
            continue

        response = input(f"  {patch['prompt']}").strip().lower()
        if response in ("y", "yes"):
            result[off:off + len(patch["replace"])] = patch["replace"]
            print(f"  [{name}] Patched at offset 0x{off:X}")
            total_applied += 1
        else:
            print(f"  [{name}] Skipped")

    if dry_run:
        print(f"\nDry run complete - {total_applied} required patch(es) would be applied.")
        print("No changes were written.")
        return True

    if total_applied > 0:
        with open(path, "wb") as f:
            f.write(result)
        print(f"\n{total_applied} patch(es) applied successfully!")
        return True

    print("\nNo patches needed (already patched or not applicable).")
    return True


def restore(path):
    backup_path = path + ".backup"
    if not os.path.exists(backup_path):
        print("No backup found. Cannot restore.")
        return False
    shutil.copy2(backup_path, path)
    print("Restored original exe from backup.")
    return True


def main():
    print("=" * 60)
    print("  Death Stranding 2 - macOS/CrossOver Compatibility Patch")
    print("=" * 60)
    print()

    args = sys.argv[1:]

    if args and args[0] == "--restore":
        path = args[1] if len(args) > 1 else find_exe()
        restore(path)
        return

    dry_run = False
    if args and args[0] == "--dry-run":
        dry_run = True
        args = args[1:]

    path = args[0] if args else find_exe()
    print(f"Target: {path}")

    if not dry_run:
        backup(path)

    success = apply_patches(path, dry_run=dry_run)

    if success:
        if dry_run:
            return
        print()
        print("Done! You can now launch DS2 via CrossOver.")
        print()
        print("Known issues:")
        print("  - Water does not render (D3DMetal shader translation bug)")
        print("  - ~15 FPS initially (improves as shader cache builds)")
        print()
        print("To restore the original exe:")
        print(f"  python3 {sys.argv[0]} --restore [path_to_DS2.exe]")
    else:
        print()
        if dry_run:
            sys.exit(1)
        print("Patching failed. Restoring backup...")
        restore(path)
        sys.exit(1)


if __name__ == "__main__":
    main()
