#!/usr/bin/env python3
"""
Death Stranding 2: On The Beach - macOS/CrossOver Compatibility Patch
=====================================================================

Applies hex patches to DS2.exe (v1.0.49.0) to bypass D3D12 feature
checks that fail on Apple's D3DMetal translation layer, allowing the
game to boot and run via CrossOver on Apple Silicon Macs.

Root Cause:
  DS2's Decima engine requires two D3D12 features during initialization
  that D3DMetal does not report as supported. Unlike HZD Remastered
  (which uses an older engine version with fallback paths), DS2 has no
  graceful fallback and crashes with "Error initializing rendering
  configuration" if either check fails.

Required Patches:
  1. DXGI_FEATURE_PRESENT_ALLOW_TEARING check
     - D3DMetal returns allow_tearing=0 (macOS handles vsync differently)
     - The game treats this as fatal with no fallback
     - Patch: NOP the conditional jump so init continues regardless

  2. D3D12_FEATURE_DATA_D3D12_OPTIONS2.DepthBoundsTestSupported check
     - D3DMetal returns DepthBoundsTestSupported=FALSE
     - The game treats this as fatal with no fallback
     - Patch: Change JNZ (conditional) to JMP (unconditional) to always continue

Optional Patch:
  3. Force HDR detection
     - D3DMetal reports SDR color space to DS2 despite supporting HDR
       (HZD Remastered correctly detects HDR on the same setup)
     - Patch: Mark the SDR color space as HDR-capable in the lookup table
     - Only apply this if your display supports HDR

Tested on:
  - macOS Sequoia, M2 Max Mac Studio, CrossOver 26, D3DMetal 3.0
  - DS2 v1.0.49.0 (Steam)

Usage:
  python3 patch_ds2.py [path_to_DS2.exe]

If no path is given, it will look in common CrossOver/Steam locations.

Author: Said (github.com/davidakh)
"""

import sys
import os
import shutil

# Required patches - needed for the game to boot
REQUIRED_PATCHES = [
    {
        "name": "DXGI_FEATURE_PRESENT_ALLOW_TEARING bypass",
        "desc": "NOP the JZ that skips rendering init when tearing is unsupported",
        "search":  bytes.fromhex("44397C243C0F84F8000000"),
        "replace": bytes.fromhex("44397C243C909090909090"),
    },
    {
        "name": "D3D12_OPTIONS2.DepthBoundsTestSupported bypass",
        "desc": "Change JNZ to JMP so init continues even without depth bounds test",
        "search":  bytes.fromhex("44397C2440750D"),
        "replace": bytes.fromhex("44397C2440EB0D"),
    },
]

# Optional patches - quality of life improvements
OPTIONAL_PATCHES = [
    {
        "name": "Force HDR detection",
        "desc": "Mark SDR color space as HDR in the lookup table so the game detects HDR",
        "prompt": "Force HDR? D3DMetal reports SDR to DS2 even on HDR displays.\n"
                  "  Only enable this if your display supports HDR. (y/N): ",
        "search":  bytes.fromhex("C645E000C745E801000000"),
        "replace": bytes.fromhex("C645E001C745E801000000"),
    },
]

DEFAULT_PATHS = [
    os.path.expanduser(
        "~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/"
        "Program Files (x86)/Steam/steamapps/common/"
        "DEATH STRANDING 2 - ON THE BEACH/ds2.exe"
    ),
    os.path.expanduser(
        "~/Library/Application Support/CrossOver/Bottles/Steam/drive_c/"
        "Program Files (x86)/Steam/steamapps/common/"
        "DEATH STRANDING 2 - ON THE BEACH/DS2.exe"
    ),
]


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
    print("  python3 patch_ds2.py /path/to/DS2.exe")
    sys.exit(1)


def backup(path):
    backup_path = path + ".backup"
    if os.path.exists(backup_path):
        print(f"Backup already exists: {backup_path}")
        return backup_path
    print(f"Creating backup: {backup_path}")
    shutil.copy2(path, backup_path)
    return backup_path


def apply_patch_list(result, data, patches):
    """Apply a list of patches to the data. Returns number of patches applied."""
    applied = 0
    for patch in patches:
        name = patch["name"]
        search = patch["search"]
        replace = patch["replace"]

        offset = data.find(search)
        if offset == -1:
            # Check if already patched
            already = data.find(replace)
            if already != -1:
                print(f"  [{name}] Already patched (offset 0x{already:X})")
                continue
            else:
                print(f"  [{name}] ERROR: Pattern not found!")
                print(f"    This exe version may not be supported.")
                print(f"    Expected bytes: {search.hex()}")
                return -1
        else:
            # Check for multiple matches
            second = data.find(search, offset + 1)
            if second != -1:
                print(f"  [{name}] WARNING: Multiple matches found!")
                print(f"    First at 0x{offset:X}, second at 0x{second:X}")
                print(f"    Patching first occurrence only.")

            result[offset:offset + len(replace)] = replace
            print(f"  [{name}] Patched at offset 0x{offset:X}")
            applied += 1

    return applied


def apply_patches(path):
    with open(path, "rb") as f:
        data = f.read()

    print(f"File size: {len(data):,} bytes")

    result = bytearray(data)
    total_applied = 0

    # Apply required patches
    print("\nRequired patches (needed to boot):")
    count = apply_patch_list(result, data, REQUIRED_PATCHES)
    if count == -1:
        return False
    total_applied += count

    # Ask about optional patches
    print("\nOptional patches:")
    for patch in OPTIONAL_PATCHES:
        name = patch["name"]
        search = patch["search"]
        replace = patch["replace"]

        # Check if already patched
        if data.find(replace) != -1:
            print(f"  [{name}] Already patched")
            continue

        # Check if pattern exists
        offset = data.find(search)
        if offset == -1:
            print(f"  [{name}] Pattern not found (version mismatch?)")
            continue

        # Ask user
        response = input(f"  {patch['prompt']}").strip().lower()
        if response in ('y', 'yes'):
            result[offset:offset + len(replace)] = replace
            print(f"  [{name}] Patched at offset 0x{offset:X}")
            total_applied += 1
        else:
            print(f"  [{name}] Skipped")

    if total_applied > 0:
        with open(path, "wb") as f:
            f.write(result)
        print(f"\n{total_applied} patch(es) applied successfully!")
        return True
    else:
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

    # Parse args
    if len(sys.argv) > 1 and sys.argv[1] == "--restore":
        path = sys.argv[2] if len(sys.argv) > 2 else find_exe()
        restore(path)
        return

    path = sys.argv[1] if len(sys.argv) > 1 else find_exe()
    print(f"Target: {path}")

    # Backup
    backup(path)

    # Apply patches
    success = apply_patches(path)

    if success:
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
        print("Patching failed. Restoring backup...")
        restore(path)
        sys.exit(1)


if __name__ == "__main__":
    main()
