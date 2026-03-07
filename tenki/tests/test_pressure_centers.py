from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "gridded_generator.py"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("tenki_gridded_generator", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PressureCenterRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_generator_module()
        cls.utc = timezone.utc

    def detect(self, name: str, key: str, run_utc: datetime, max_step: int, valid_iso: str):
        model = self.mod.ModelRun(name, key, run_utc, max_step)
        valid = datetime.fromisoformat(valid_iso)
        try:
            lons, lats, values = self.mod.load_grid(model, valid)
        except Exception as exc:  # pragma: no cover - integration skip path
            self.skipTest(f"cached grid unavailable for {key} {valid_iso}: {exc}")
        return self.mod.detect_pressure_centers(lons, lats, values)

    def assertHasCenter(self, centers, kind: str, lon: float, lat: float):
        for center in centers:
            if center.kind != kind:
                continue
            if abs(center.lon - lon) <= 1.25 and abs(center.lat - lat) <= 1.25:
                return
        self.fail(f"missing {kind} center near ({lon}, {lat}); got {centers}")

    def assertLacksCenter(self, centers, kind: str, lon: float, lat: float):
        for center in centers:
            if center.kind != kind:
                continue
            if abs(center.lon - lon) <= 1.25 and abs(center.lat - lat) <= 1.25:
                self.fail(f"unexpected {kind} center near ({lon}, {lat}); got {centers}")

    def test_ecmwf_closed_low_kept(self):
        centers = self.detect(
            "ECMWF",
            "ecmwf",
            datetime(2026, 3, 7, 0, tzinfo=self.utc),
            144,
            "2026-03-09T09:00:00+09:00",
        )
        self.assertHasCenter(centers, "low", 131.75, 37.0)

    def test_ecmwf_main_low_kept_without_false_high(self):
        centers = self.detect(
            "ECMWF",
            "ecmwf",
            datetime(2026, 3, 7, 0, tzinfo=self.utc),
            144,
            "2026-03-11T06:00:00+09:00",
        )
        self.assertHasCenter(centers, "low", 150.0, 40.0)
        self.assertLacksCenter(centers, "high", 139.25, 37.25)

    def test_gfs_closed_low_kept(self):
        centers = self.detect(
            "GFS",
            "gfs",
            datetime(2026, 3, 7, 0, tzinfo=self.utc),
            384,
            "2026-03-09T03:00:00+09:00",
        )
        self.assertHasCenter(centers, "low", 131.25, 37.0)

    def test_gfs_false_high_removed(self):
        centers = self.detect(
            "GFS",
            "gfs",
            datetime(2026, 3, 7, 0, tzinfo=self.utc),
            384,
            "2026-03-11T06:00:00+09:00",
        )
        self.assertLacksCenter(centers, "high", 139.25, 37.0)
        self.assertHasCenter(centers, "high", 119.25, 45.75)

    def test_gfs_false_low_removed(self):
        centers = self.detect(
            "GFS",
            "gfs",
            datetime(2026, 3, 7, 0, tzinfo=self.utc),
            384,
            "2026-03-12T12:00:00+09:00",
        )
        self.assertHasCenter(centers, "low", 154.25, 45.75)
        self.assertLacksCenter(centers, "low", 138.0, 35.75)


if __name__ == "__main__":
    unittest.main()
