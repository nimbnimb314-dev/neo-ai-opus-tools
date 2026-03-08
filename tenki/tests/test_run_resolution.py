from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "gridded_generator.py"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("tenki_gridded_generator_run_resolution", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_generator_module()
        cls.utc = timezone.utc

    def patch_attr(self, name: str, value):
        original = getattr(self.mod, name)
        setattr(self.mod, name, value)
        self.addCleanup(setattr, self.mod, name, original)

    def test_resolve_runs_uses_cached_run_when_live_lookup_fails(self):
        cached_gfs = self.mod.ModelRun("GFS", "gfs", datetime(2026, 3, 7, 0, tzinfo=self.utc), 384)
        saved = {}

        self.patch_attr("load_cached_runs", lambda: {"gfs": cached_gfs})
        self.patch_attr("save_cached_runs", lambda runs: saved.update(runs))
        self.patch_attr("resolve_ecmwf_run", lambda: self.mod.ModelRun("ECMWF", "ecmwf", datetime(2026, 3, 7, 0, tzinfo=self.utc), 240))
        self.patch_attr("resolve_icon_run", lambda: self.mod.ModelRun("ICON", "icon", datetime(2026, 3, 7, 0, tzinfo=self.utc), 180))

        def fail_gfs():
            raise RuntimeError("nomads unavailable")

        self.patch_attr("resolve_gfs_run", fail_gfs)

        resolved, warnings = self.mod.resolve_runs()

        self.assertEqual(resolved["gfs"].model, cached_gfs)
        self.assertEqual(resolved["gfs"].source, "cache")
        self.assertIn("nomads unavailable", resolved["gfs"].note)
        self.assertIn("gfs", saved)
        self.assertEqual(saved["gfs"], cached_gfs)
        self.assertTrue(any("gfs: live run lookup failed" in warning for warning in warnings))

    def test_resolve_runs_skips_missing_model_when_other_models_exist(self):
        saved = {}

        self.patch_attr("load_cached_runs", lambda: {})
        self.patch_attr("save_cached_runs", lambda runs: saved.update(runs))
        self.patch_attr("resolve_ecmwf_run", lambda: self.mod.ModelRun("ECMWF", "ecmwf", datetime(2026, 3, 7, 0, tzinfo=self.utc), 240))
        self.patch_attr("resolve_icon_run", lambda: self.mod.ModelRun("ICON", "icon", datetime(2026, 3, 7, 0, tzinfo=self.utc), 180))

        def fail_gfs():
            raise RuntimeError("nomads unavailable")

        self.patch_attr("resolve_gfs_run", fail_gfs)

        resolved, warnings = self.mod.resolve_runs()

        self.assertIn("ecmwf", resolved)
        self.assertIn("icon", resolved)
        self.assertNotIn("gfs", resolved)
        self.assertEqual(set(saved), {"ecmwf", "icon"})
        self.assertTrue(any("gfs: skipped because run lookup failed" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
