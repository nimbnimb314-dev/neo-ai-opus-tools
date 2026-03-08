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

    def make_candidate(self, kind: str, value: float, prominence: float, area: int = 30, merged: bool = False):
        return self.mod.PersistentCenterCandidate(
            kind=kind,
            lon=140.0,
            lat=35.0,
            value=value,
            prominence=prominence,
            closed_levels=2,
            max_area=area,
            merged_with_stronger=merged,
        )

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

    def test_should_keep_candidate_rejects_high_pressure_low(self):
        candidate = self.make_candidate("low", value=1020.0, prominence=3.6, merged=True, area=80)
        self.assertFalse(self.mod.should_keep_candidate(candidate, field_reference=1014.0))

    def test_should_keep_candidate_promotes_high_pressure_high(self):
        candidate = self.make_candidate("high", value=1028.0, prominence=1.9, area=24)
        self.assertTrue(self.mod.should_keep_candidate(candidate, field_reference=1018.0))

    def test_crop_regular_grid_uses_expanded_data_region(self):
        lons = self.mod.np.array([93.0, 94.0, 178.0, 179.0])
        lats = self.mod.np.array([11.0, 12.0, 60.0, 61.0])
        values = self.mod.np.arange(16, dtype=float).reshape(4, 4)

        cropped_lons, cropped_lats, cropped_values = self.mod.crop_regular_grid(lons, lats, values)

        self.assertEqual(cropped_lons.tolist(), [94.0, 178.0])
        self.assertEqual(cropped_lats.tolist(), [12.0, 60.0])
        self.assertEqual(cropped_values.shape, (2, 2))

    def test_data_region_covers_visible_plot_corners(self):
        x_min, x_max, y_min, y_max = self.mod.projected_region_bounds()
        pad_x = (x_max - x_min) * self.mod.PLOT_PAD_X_RATIO
        pad_y = (y_max - y_min) * self.mod.PLOT_PAD_Y_RATIO
        corners = [
            (x_min - pad_x, y_max + pad_y),
            (x_max + pad_x, y_max + pad_y),
            (x_min - pad_x, y_min - pad_y),
            (x_max + pad_x, y_min - pad_y),
        ]

        for x_coord, y_coord in corners:
            lon, lat = self.mod.inverse_project_coords(x_coord, y_coord)
            self.assertGreaterEqual(lon, self.mod.DATA_REGION["lon_min"])
            self.assertLessEqual(lon, self.mod.DATA_REGION["lon_max"])
            self.assertGreaterEqual(lat, self.mod.DATA_REGION["lat_min"])
            self.assertLessEqual(lat, self.mod.DATA_REGION["lat_max"])

    def test_is_within_plot_region_excludes_data_margin_only_centers(self):
        self.assertTrue(self.mod.is_within_plot_region(114.0, 19.0))
        self.assertTrue(self.mod.is_within_plot_region(158.0, 52.0))
        self.assertFalse(self.mod.is_within_plot_region(110.0, 30.0))
        self.assertFalse(self.mod.is_within_plot_region(160.5, 30.0))

    def test_smooth_contour_values_preserves_shape_and_finite_values(self):
        values = self.mod.np.array(
            [
                [1000.0, 1004.0, 1008.0],
                [1002.0, 1006.0, 1010.0],
                [1004.0, 1008.0, 1012.0],
            ]
        )

        smoothed = self.mod.smooth_contour_values(values)

        self.assertEqual(smoothed.shape, values.shape)
        self.assertTrue(self.mod.np.isfinite(smoothed).all())
        self.assertNotEqual(float(smoothed[1, 1]), float(values[1, 1]))


if __name__ == "__main__":
    unittest.main()
