"""Regression tests for the staging safety of scripts/prepare_tk_data.py.

These tests build malicious and valid zip archives in bounded temporary
directories and assert that archive-controlled prefixes can never cause a
recursive delete outside ``<build_dir>/tk_staging`` while real Tcl/Tk
staging still works.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "prepare_tk_data.py"
_spec = importlib.util.spec_from_file_location("prepare_tk_data", _SCRIPT)
assert _spec is not None and _spec.loader is not None
prepare_tk_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare_tk_data)


def _write_zip(path: Path, members: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return path


def _fake_installation(root: Path, tcl_members: dict, tk_members: dict) -> Path:
    """Create a fake Python prefix containing only the two Tcl/Tk archives."""
    prefix = root / "fake-python"
    _write_zip(prefix / "tcl" / "libtcl9.0.4.zip", tcl_members)
    _write_zip(prefix / "tcl" / "libtk9.0.4.zip", tk_members)
    return prefix


class ComponentValidationTests(unittest.TestCase):
    def test_rejects_unsafe_single_components(self):
        unsafe = (
            "",
            ".",
            "..",
            "a/b",
            "a\\b",
            "D:evil",
            "C:",
            "CON",
            "con.txt",
            "NUL.dat",
            "AUX",
            "COM1",
            "lpt9.tcl",
            "CLOCK$",
            "trailing.",
            "trailing ",
            "nul\x00byte",
        )
        for name in unsafe:
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare_tk_data._validate_component(name, context="test component")

    def test_accepts_ordinary_components(self):
        for name in ("tcl_library", "tk_library", "sub-dir", "a.b", "COM10", "CONSOLE"):
            with self.subTest(name=name):
                self.assertEqual(
                    prepare_tk_data._validate_component(name, context="test component"), name
                )


class StagingSafetyTests(unittest.TestCase):
    def _make_build_dir(self, root: Path) -> tuple[Path, Path]:
        build_dir = root / "build"
        staging_root = build_dir / "tk_staging"
        staging_root.mkdir(parents=True)
        (build_dir / "build-sentinel.txt").write_text("build", encoding="utf-8")
        (staging_root / "staging-sentinel.txt").write_text("staging", encoding="utf-8")
        (root / "outer-sentinel.txt").write_text("outer", encoding="utf-8")
        return build_dir, staging_root

    def _assert_sentinels_intact(self, root: Path, build_dir: Path, staging_root: Path):
        self.assertEqual((build_dir / "build-sentinel.txt").read_text(encoding="utf-8"), "build")
        self.assertEqual(
            (staging_root / "staging-sentinel.txt").read_text(encoding="utf-8"), "staging"
        )
        self.assertEqual((root / "outer-sentinel.txt").read_text(encoding="utf-8"), "outer")

    def test_malicious_archive_prefix_cannot_delete_outside_staging(self):
        malicious_prefixes = ("..", "D:evil", "CON", "nested/evil")
        for prefix in malicious_prefixes:
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build_dir, staging_root = self._make_build_dir(root)
                fake_prefix = _fake_installation(
                    root,
                    {f"{prefix}/init.tcl": "malicious tcl"},
                    {f"{prefix}/tk.tcl": "malicious tk"},
                )

                with self.assertRaises(ValueError):
                    prepare_tk_data.prepare(build_dir, base_prefix=fake_prefix, sys_prefix=fake_prefix)

                self._assert_sentinels_intact(root, build_dir, staging_root)
                self.assertFalse((build_dir / "init.tcl").exists())
                self.assertFalse((root / "init.tcl").exists())
                self.assertFalse((build_dir / "tcl_library").exists())

    def test_member_traversal_is_rejected_without_deleting_sentinels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir, staging_root = self._make_build_dir(root)
            fake_prefix = _fake_installation(
                root,
                {"tcl_library/init.tcl": "tcl", "tcl_library/../../escape.txt": "evil"},
                {"tk_library/tk.tcl": "tk"},
            )

            with self.assertRaises(ValueError):
                prepare_tk_data.prepare(build_dir, base_prefix=fake_prefix, sys_prefix=fake_prefix)

            self._assert_sentinels_intact(root, build_dir, staging_root)
            self.assertFalse((build_dir / "escape.txt").exists())
            self.assertFalse((staging_root / "escape.txt").exists())

    def test_symlink_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir, staging_root = self._make_build_dir(root)
            fake_prefix = root / "fake-python"
            tcl_archive = _write_zip(
                fake_prefix / "tcl" / "libtcl9.0.4.zip", {"tcl_library/init.tcl": "tcl"}
            )
            with zipfile.ZipFile(tcl_archive, "a") as archive:
                link = zipfile.ZipInfo("tcl_library/link.tcl")
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, "C:/Windows/System32")
            _write_zip(fake_prefix / "tcl" / "libtk9.0.4.zip", {"tk_library/tk.tcl": "tk"})

            with self.assertRaises(ValueError):
                prepare_tk_data.prepare(build_dir, base_prefix=fake_prefix, sys_prefix=fake_prefix)

            self._assert_sentinels_intact(root, build_dir, staging_root)
            self.assertFalse((staging_root / "tcl_library" / "link.tcl").exists())

    def test_symlinked_target_is_rejected_before_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir, staging_root = self._make_build_dir(root)
            outside = root / "outside"
            outside.mkdir()
            (outside / "init.tcl").write_text("outside", encoding="utf-8")
            link = staging_root / "tcl_library"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                if os.name != "nt":
                    self.skipTest("symlinks are not available in this environment")
                # Directory junctions need no elevated privilege on Windows
                # and still resolve outside the staging root.
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    self.skipTest(f"could not create a junction: {completed.stderr.strip()}")
            archive = _write_zip(root / "tcl.zip", {"tcl_library/init.tcl": "new"})

            with self.assertRaises(ValueError):
                prepare_tk_data._safe_extract(
                    archive, "tcl_library", "tcl_library", staging_root, "init.tcl"
                )

            self.assertEqual((outside / "init.tcl").read_text(encoding="utf-8"), "outside")
            self.assertTrue(link.is_symlink() or os.path.isjunction(link))

    def test_valid_zip_staging_extracts_into_fixed_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir, staging_root = self._make_build_dir(root)
            stale = staging_root / "tcl_library" / "stale.tcl"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")
            fake_prefix = _fake_installation(
                root,
                {
                    "tcl_library/init.tcl": "proc tclInit {} {}",
                    "tcl_library/encoding/ascii.enc": "ascii",
                },
                {"tk_library/tk.tcl": "# tk", "tk_library/images/logo.gif": "gif"},
            )

            result = prepare_tk_data.prepare(build_dir, base_prefix=fake_prefix, sys_prefix=fake_prefix)

            self.assertTrue(result["zip_based"])
            self.assertEqual(result["tcl_library"], str(staging_root / "tcl_library"))
            self.assertEqual(result["tk_library"], str(staging_root / "tk_library"))
            self.assertEqual(result["tcl_files"], 2)
            self.assertEqual(result["tk_files"], 2)
            self.assertEqual(
                (staging_root / "tcl_library" / "init.tcl").read_text(encoding="utf-8"),
                "proc tclInit {} {}",
            )
            self.assertEqual(
                (staging_root / "tcl_library" / "encoding" / "ascii.enc").read_text(
                    encoding="utf-8"
                ),
                "ascii",
            )
            self.assertEqual(
                (staging_root / "tk_library" / "tk.tcl").read_text(encoding="utf-8"), "# tk"
            )
            self.assertFalse(stale.exists())
            self._assert_sentinels_intact(root, build_dir, staging_root)


if __name__ == "__main__":
    unittest.main()
