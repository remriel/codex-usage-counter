#!/usr/bin/env python3
"""Stage zip-packaged Tcl/Tk library data for PyInstaller.

Python 3.14 on Windows ships Tcl/Tk as zipped archives in the base
installation (``<base_prefix>/tcl/libtcl9.0.4.zip`` and
``libtk9.0.4.zip``) whose members live under ``tcl_library/`` and
``tk_library/``.  tkinter reports those library directories as
``//zipfs:/...`` paths, which PyInstaller's Tcl/Tk hooks do not collect,
so the frozen executable is missing ``_tcl_data``/``_tk_data`` and exits
immediately with an error from ``pyi_rth__tkinter.py``.

This helper discovers the archives for the *current* interpreter
(``sys.base_prefix``), safely extracts the library members into
``<build_dir>/tk_staging/tcl_library`` and ``.../tk_library``, and prints
a single JSON object on stdout for ``build.ps1`` to consume::

    {"zip_based": true, "tcl_library": "...", "tk_library": "..."}

On normal (non-zip) Tcl/Tk installations no archives are found; the
helper then prints ``zip_based: false`` with null paths and does not
stage anything, leaving PyInstaller's own hooks to collect the data
directly from disk.

Only the Python standard library is used, and nothing outside the given
build directory is created, modified, or deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

TCL_MARKER = "init.tcl"
TK_MARKER = "tk.tcl"


def _find_client_archives(base_prefix: Path) -> dict[str, Path]:
    """Return {"tcl": path, "tk": path} for zip archives under *base_prefix*.

    Archives are looked for in the usual Windows layout (``tcl/``) and a
    couple of portable-install fallbacks (``lib/``, ``libs/``).  When
    several matching archives exist, the last one in sorted order wins.
    Missing entries are simply absent from the result.
    """
    found: dict[str, Path] = {}
    for subdir in ("tcl", "lib", "libs"):
        candidate_dir = base_prefix / subdir
        if not candidate_dir.is_dir():
            continue
        for entry in sorted(candidate_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".zip":
                continue
            stem = entry.stem.lower()
            if stem.startswith(("libtk", "tk")):
                kind = "tk"
            elif stem.startswith(("libtcl", "tcl")):
                kind = "tcl"
            else:
                continue
            found[kind] = entry
    return found


def _locate_library_prefix(zf: zipfile.ZipFile, marker: str) -> str:
    """Return the single top-level member directory containing *marker*."""
    prefix = None
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if parts and parts[-1] == marker and len(parts) >= 2:
            candidate = parts[0]
            if prefix is not None and candidate != prefix:
                raise ValueError(
                    f"archive contains multiple top-level directories with {marker!r}: "
                    f"{prefix!r} and {candidate!r}"
                )
            prefix = candidate
    if prefix is None:
        raise ValueError(f"archive does not contain a {marker!r} library member")
    return prefix


_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CLOCK$"]
    + [f"COM{index}" for index in range(1, 10)]
    + [f"LPT{index}" for index in range(1, 10)]
)


def _validate_component(name: str, *, context: str) -> str:
    """Return *name* if it is a single, safe, ordinary path component.

    Rejects empty names, ``.``/``..``, any path separator, drive
    letters/colons, NUL bytes, trailing dots or spaces, and Windows
    reserved device names (with or without an extension).  Used for the
    archive-controlled library prefix and for staging target names
    before anything is created or recursively deleted.
    """
    if not name or name in (".", ".."):
        raise ValueError(f"unsafe {context} {name!r}")
    if any(char in name for char in ("/", "\\", ":", "\x00")):
        raise ValueError(f"unsafe {context} {name!r}: path separators are not allowed")
    if name[-1] in (".", " "):
        raise ValueError(f"unsafe {context} {name!r}: trailing dots or spaces are not allowed")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"unsafe {context} {name!r}: reserved Windows device name")
    return name


def _is_strict_child(path: Path, root: Path) -> bool:
    """Return True when resolved *path* lies strictly below resolved *root*."""
    try:
        return bool(path.relative_to(root).parts)
    except ValueError:
        return False


def _safe_extract(
    zip_path: Path,
    member_prefix: str,
    target_name: str,
    staging_root: Path,
    marker: str,
) -> int:
    """Extract ``member_prefix/`` from *zip_path* into ``staging_root/target_name``.

    ``member_prefix`` comes from the archive, so it is validated as a
    single safe path component and is only used to select members; the
    directory that gets deleted is the fixed, validated *target_name*.
    The resolved target must be a strict child of the resolved staging
    root before any recursive deletion, and symlink targets are
    rejected.  Every member is validated to stay within the staging root
    before it is written; absolute paths, drive letters, ``..``
    components and symbolic links are rejected.  The target directory is
    recreated so a previous staging run cannot leave stale files behind.
    Returns the number of files extracted.
    """
    _validate_component(member_prefix, context=f"library prefix in {zip_path}")
    staging_root = staging_root.resolve()
    target_dir = staging_root / _validate_component(target_name, context="staging target")
    if target_dir.is_symlink():
        raise ValueError(f"refusing to replace symlinked staging target {target_dir}")
    if not _is_strict_child(target_dir.resolve(), staging_root):
        raise ValueError(f"staging target {target_dir} resolves outside {staging_root}")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    prefix = member_prefix.rstrip("/") + "/"
    extracted = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix):]
            if not relative or relative.endswith("/"):
                continue
            if os.path.isabs(relative) or relative[1:2] == ":":
                raise ValueError(f"unsafe absolute member {info.filename!r} in {zip_path}")
            parts = [part for part in relative.split("/") if part not in ("", ".")]
            if not parts or ".." in parts or any(":" in part for part in parts):
                raise ValueError(f"unsafe member path {info.filename!r} in {zip_path}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"refusing symbolic-link member {info.filename!r} in {zip_path}")

            destination = target_dir.joinpath(*parts)
            if not _is_strict_child(destination.resolve(), staging_root):
                raise ValueError(
                    f"member {info.filename!r} in {zip_path} would escape the staging directory"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, open(destination, "wb") as output:
                shutil.copyfileobj(source, output)
            extracted += 1

    if not _is_strict_child(target_dir.resolve(), staging_root):
        raise ValueError(f"staging target {target_dir} resolves outside {staging_root}")
    marker_path = target_dir / marker
    if not marker_path.is_file():
        raise ValueError(f"staged files from {zip_path} are missing required {marker!r}")
    return extracted


def prepare(build_dir: Path, base_prefix: Path | None = None, sys_prefix: Path | None = None) -> dict:
    """Discover and stage zip-based Tcl/Tk data.

    Returns the JSON-serializable result dict.  Raises ``ValueError`` on
    a malformed or unsafe archive and ``FileNotFoundError`` if only one
    of the two expected archives is present.
    """
    base_prefix = Path(base_prefix if base_prefix is not None else sys.base_prefix)
    prefix = Path(sys_prefix if sys_prefix is not None else sys.prefix)
    staging_root = build_dir / "tk_staging"

    archives: dict[str, Path] = {}
    for current in (base_prefix, prefix):
        for kind, path in _find_client_archives(current).items():
            archives.setdefault(kind, path)

    # A normal, unzipped installation is handled entirely by PyInstaller.
    if not archives:
        return {"zip_based": False, "tcl_library": None, "tk_library": None}

    missing = [kind for kind in ("tcl", "tk") if kind not in archives]
    if missing:
        found = ", ".join(f"{kind}={path}" for kind, path in sorted(archives.items()))
        raise FileNotFoundError(
            f"found zip-based {found} but no {'/'.join(missing)} archive under "
            f"{base_prefix or prefix!s}; refusing to stage a partial Tcl/Tk runtime"
        )

    result = {"zip_based": True, "tcl_library": None, "tk_library": None}
    for kind, marker in (("tcl", TCL_MARKER), ("tk", TK_MARKER)):
        archive = archives[kind]
        with zipfile.ZipFile(archive) as zf:
            member_prefix = _locate_library_prefix(zf, marker)
        # Never derive the directory that gets deleted from the archive:
        # use a fixed, validated staging name and keep the archive prefix
        # only as a member filter.
        target_name = f"{kind}_library"
        count = _safe_extract(archive, member_prefix, target_name, staging_root, marker)
        target_dir = staging_root / target_name
        result[f"{kind}_library"] = str(target_dir)
        result[f"{kind}_source"] = str(archive)
        result[f"{kind}_files"] = count
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "build",
        help="build directory that receives tk_staging/ (default: <project>/build)",
    )
    args = parser.parse_args(argv)

    try:
        result = prepare(args.build_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"prepare_tk_data: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
