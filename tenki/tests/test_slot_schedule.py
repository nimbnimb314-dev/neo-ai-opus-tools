from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "gridded_generator.py"
JST = timezone(timedelta(hours=9))
UTC = timezone.utc


def load_generator_module():
    spec = importlib.util.spec_from_file_location("tenki_gridded_generator_slot_schedule", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SlotScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_generator_module()

    def test_build_slots_reduces_density_after_three_days(self):
        slots = self.mod.build_slots(datetime(2026, 3, 7, 18, 10, tzinfo=JST))
        self.assertEqual(slots[0].slot_id, "20260307T2100")
        self.assertEqual(slots[-1].slot_id, "20260317T2100")

        first_sparse = datetime(2026, 3, 10, 21, 0, tzinfo=JST)
        sparse_slots = [slot for slot in slots if slot.valid_jst >= first_sparse]
        self.assertTrue(sparse_slots)
        self.assertTrue(all(slot.valid_jst.hour in {9, 21} for slot in sparse_slots))

        detailed_slots = [slot for slot in slots if slot.valid_jst < first_sparse]
        self.assertTrue(any(slot.valid_jst.hour == 0 for slot in detailed_slots))
        self.assertTrue(any(slot.valid_jst.hour == 15 for slot in detailed_slots))

    def test_ecmwf_main_run_allows_extended_six_hour_steps(self):
        run_utc = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
        self.assertEqual(self.mod.ecmwf_step_for(144, run_utc), 144)
        self.assertEqual(self.mod.ecmwf_step_for(150, run_utc), 150)
        self.assertEqual(self.mod.ecmwf_step_for(240, run_utc), 240)
        self.assertIsNone(self.mod.ecmwf_step_for(147, run_utc))
        self.assertIsNone(self.mod.ecmwf_step_for(243, run_utc))

    def test_preferred_ecmwf_run_uses_previous_main_cycle_for_extended_range(self):
        latest_run = datetime(2026, 3, 7, 6, 0, tzinfo=UTC)
        preferred = self.mod.preferred_ecmwf_run(latest_run)
        self.assertEqual(preferred, datetime(2026, 3, 7, 0, 0, tzinfo=UTC))
        self.assertEqual(self.mod.ecmwf_run_profile(preferred), ("oper", 240))


if __name__ == "__main__":
    unittest.main()
