"""Copy files from every subfolder under a parent folder onto the Desktop.

Point ``--root`` at any parent folder. Every immediate subfolder is processed
(names do not matter).

Selection (overall ``--cap-gb``, default 15GB):

  1. Per subfolder — take up to ``--limit`` files (default 1000; or whatever
     the folder has if less), walk order, whatever comes first.
  2. If the total is still under 15GB — keep taking more files from those
     folders until the cap is reached.

Copies to::

    ~/Desktop/AI Labs Sample Set (YYYY-MM-DD)/
      <subfolder-name>/
      ...

Usage::

  python tools/build_quality_sample_local.py --root ~/Downloads/company-dump
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SKIP_DIR_NAMES = {".venv", "__pycache__", "node_modules", ".git"}
GB = 1024 ** 3


def _log(msg: str) -> None:
    print(msg, flush=True)


def _parse_names(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        for part in item.split(","):
            name = part.strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
    return out


def default_desktop() -> Path:
    return Path.home() / "Desktop"


def list_subfolders(root: Path, only: list[str] | None = None) -> list[Path]:
    """Every immediate child directory of ``root`` (dot-dirs skipped). Names unrestricted."""
    if not root.is_dir():
        raise FileNotFoundError(f"root folder not found: {root}")
    dirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )
    if only:
        wanted = {n.lower() for n in only}
        dirs = [p for p in dirs if p.name.lower() in wanted]
        missing = wanted - {p.name.lower() for p in dirs}
        if missing:
            _log(f"WARNING: no subfolder for: {', '.join(sorted(missing))}")
    return dirs


list_account_dirs = list_subfolders  # back-compat


def list_files(folder: Path) -> list[tuple[Path, int]]:
    """All files under ``folder`` in walk order, with sizes. Skips empty / unreadable."""
    found: list[tuple[Path, int]] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIR_NAMES and not d.startswith(".")
        ]
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            if fn.startswith("."):
                continue
            path = Path(dirpath) / fn
            if not path.is_file():
                continue
            try:
                size = int(path.stat().st_size)
            except OSError:
                continue
            if size <= 0:
                continue
            found.append((path, size))
    return found


def select_files_two_phase(
    by_folder: dict[str, list[tuple[Path, int]]],
    *,
    limit_per_folder: int,
    cap_bytes: int,
) -> list[tuple[str, Path, int]]:
    """Phase 1: up to ``limit_per_folder`` per folder. Phase 2: more until ``cap_bytes``.

    Walk order within each folder. Files that don't fit the remaining cap are skipped;
    selection keeps looking for ones that do. Returns ``(folder_name, path, size)``.
    """
    selected: list[tuple[str, Path, int]] = []
    used = 0
    names = list(by_folder.keys())
    cursors = {name: 0 for name in names}
    phase1_counts = {name: 0 for name in names}

    def _take_one(name: str) -> bool:
        """Advance cursor; if a file fits, append it and return True. Exhaust → False."""
        nonlocal used
        files = by_folder[name]
        while cursors[name] < len(files):
            path, size = files[cursors[name]]
            cursors[name] += 1
            if used + size <= cap_bytes:
                selected.append((name, path, size))
                used += size
                return True
            # skip — doesn't fit; try next in this folder
        return False

    # Phase 1 — up to limit_per_folder each
    for name in names:
        while phase1_counts[name] < limit_per_folder:
            if used >= cap_bytes:
                return selected
            if not _take_one(name):
                break
            phase1_counts[name] += 1

    # Phase 2 — keep filling until cap (round-robin across folders)
    while used < cap_bytes:
        progressed = False
        for name in names:
            if used >= cap_bytes:
                break
            if cursors[name] >= len(by_folder[name]):
                continue
            if _take_one(name):
                progressed = True
        if not progressed:
            break

    return selected


def first_n_files(folder: Path, limit: int) -> list[Path]:
    """Back-compat helper for older tests."""
    return [p for p, _ in list_files(folder)[:limit]]


def select_files(folder: Path, *, limit: int, cap_bytes: int) -> list[tuple[Path, int]]:
    """Back-compat: single-folder two-phase collapses to one phase when limit covers all."""
    picked = select_files_two_phase({"_": list_files(folder)}, limit_per_folder=limit, cap_bytes=cap_bytes)
    return [(path, size) for _name, path, size in picked]


def dest_folder_name(folder_name: str) -> str:
    if folder_name == "AI Labs Sample Set" or not folder_name.strip():
        return f"AI Labs Sample Set ({datetime.now().strftime('%Y-%m-%d')})"
    return folder_name


def copy_one(src: Path, source_folder: Path, dest_folder: Path) -> bool:
    rel = src.relative_to(source_folder)
    dst = dest_folder / rel
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except OSError as exc:
        _log(f"FAIL {src}: {exc}")
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", required=True, metavar="DIR",
                    help="Parent folder — every immediate subfolder is processed")
    p.add_argument("--limit", type=int, default=1000,
                    help="Files to take per subfolder first, before topping up to the GB cap "
                         "(default: 1000)")
    p.add_argument("--cap-gb", type=float, default=15.0,
                    help="Overall byte cap in GB (default: 15). After the per-folder limit, "
                         "more files are taken until this is reached.")
    p.add_argument("--folder-name", default="AI Labs Sample Set",
                    help="Destination folder name on the Desktop")
    p.add_argument("--dest", default="",
                    help="Full destination path (default: ~/Desktop/<folder-name>)")
    p.add_argument("--only", nargs="+", metavar="NAME",
                    help="Optional: only these subfolder names (space- or comma-separated)")
    p.add_argument("--users", nargs="+", metavar="NAME",
                    help=argparse.SUPPRESS)
    p.add_argument("--out", default="out/quality_sample_local_manifest.json",
                    help="Optional manifest of what was copied")
    args = p.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: --root not found or not a directory: {root}", flush=True)
        return 1
    if args.limit < 1:
        print("ERROR: --limit must be >= 1", flush=True)
        return 1
    if args.cap_gb <= 0:
        print("ERROR: --cap-gb must be > 0", flush=True)
        return 1

    if args.dest:
        dest_root = Path(args.dest).expanduser().resolve()
    else:
        dest_root = (default_desktop() / dest_folder_name(args.folder_name)).resolve()

    try:
        dest_root.relative_to(root)
        print(f"ERROR: --dest must not be inside --root ({dest_root} is under {root})", flush=True)
        return 1
    except ValueError:
        pass

    only = _parse_names(args.only) or _parse_names(args.users) or None
    subfolders = list_subfolders(root, only)
    if not subfolders:
        print(f"ERROR: no subfolders under {root}", flush=True)
        return 1

    cap_bytes = int(args.cap_gb * GB)
    _log(f"{len(subfolders)} subfolder(s) → first {args.limit}/folder, then fill to "
         f"{args.cap_gb:g}GB → {dest_root}")

    folder_by_name = {f.name: f for f in subfolders}
    by_folder: dict[str, list[tuple[Path, int]]] = {}
    for folder in subfolders:
        files = list_files(folder)
        by_folder[folder.name] = files
        _log(f"  listed {folder.name}: {len(files)} file(s), "
             f"{sum(s for _, s in files) / GB:.2f}GB")

    selected = select_files_two_phase(
        by_folder, limit_per_folder=args.limit, cap_bytes=cap_bytes,
    )
    total_bytes = sum(sz for _, _, sz in selected)
    _log(f"selected {len(selected)} file(s), {total_bytes / GB:.2f}GB / {args.cap_gb:g}GB")

    dest_root.mkdir(parents=True, exist_ok=True)
    manifest_files: list[dict] = []
    total_ok = total_fail = 0
    per_folder_counts: dict[str, int] = {}
    for name, src, size in selected:
        source_folder = folder_by_name[name]
        dest_sub = dest_root / name
        if copy_one(src, source_folder, dest_sub):
            total_ok += 1
            per_folder_counts[name] = per_folder_counts.get(name, 0) + 1
        else:
            total_fail += 1
        rel = src.relative_to(source_folder).as_posix()
        manifest_files.append({
            "name": src.name,
            "path": f"{name}/{rel}",
            "folder": name,
            "size_bytes": size,
        })

    for name in by_folder:
        _log(f"  {name}: copied {per_folder_counts.get(name, 0)}")

    out_path = (_ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scan_mode": "local_first_n_then_cap",
        "root": str(root),
        "limit_per_folder": args.limit,
        "cap_bytes": cap_bytes,
        "total_bytes": total_bytes,
        "dest": str(dest_root),
        "files": manifest_files,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    _log(f"done: ok={total_ok} fail={total_fail} bytes={total_bytes / GB:.2f}GB / {args.cap_gb:g}GB → {dest_root}")
    _log(f"manifest: {out_path}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
