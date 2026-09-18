#!/usr/bin/env python3
"""Sanity-check the patch tables in a ds2.py before it ships.

A search/replace length mismatch would shift every following byte and corrupt
the executable, so that invariant is worth enforcing in CI rather than
discovering on someone's 117 MB game binary.
"""
import importlib.util
import pathlib
import sys


def load(path):
    spec = importlib.util.spec_from_file_location("ds2_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # safe: ds2.py guards main() behind __main__
    return mod


def groups_of(mod):
    out = []
    for build, spec in getattr(mod, "BUILDS", {}).items():
        for key in ("required", "optional"):
            if spec.get(key):
                out.append((f"{build} {key}", spec[key]))
    for name in ("REQUIRED_PATCHES", "OPTIONAL_PATCHES", "PATCHES"):
        if hasattr(mod, name):
            out.append((name, getattr(mod, name)))
    return out


def check(path):
    print(f"== {path}")
    mod = load(path)
    groups = groups_of(mod)
    if not groups:
        print("   FAIL: no patch definitions found")
        return 1

    bad = 0
    for label, patches in groups:
        for p in patches:
            name = p.get("name", "<unnamed>")
            search, replace = p["search"], p["replace"]

            if len(search) != len(replace):
                print(f"   FAIL {label}: {name} — "
                      f"search {len(search)}B vs replace {len(replace)}B")
                bad += 1
            elif search == replace:
                print(f"   FAIL {label}: {name} — search == replace (no-op)")
                bad += 1
            elif not search:
                print(f"   FAIL {label}: {name} — empty pattern")
                bad += 1
            else:
                print(f"   ok   {label}: {name} ({len(search)}B)")
    return bad


def main(argv):
    paths = [pathlib.Path(p) for p in argv[1:]]
    if not paths:
        print("usage: validate_patches.py <ds2.py> [...]", file=sys.stderr)
        return 2

    bad = sum(check(p) for p in paths)
    print()
    print(f"{'FAILED' if bad else 'PASSED'} — {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
