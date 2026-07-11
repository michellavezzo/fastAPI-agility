import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.chrono import Chronometer


class ChronometerCalibrationTest(unittest.TestCase):
    def make_chronometer(self, existing_result):
        chrono = object.__new__(Chronometer)
        chrono._lock = threading.RLock()
        chrono._calibration_lock = threading.Lock()
        chrono._calibration_running = True
        chrono._calibration_last_result = existing_result
        chrono._calibration_status = {
            "running": True,
            "phase": "active_scan",
            "frequency_hz": 50000,
            "started_at": "2026-07-10T10:00:00",
            "finished_at": None,
            "trigger": "test",
            "error": None,
            "last_attempt": None,
            "last_result": existing_result,
        }
        chrono._mark_state_changed_locked = lambda: None
        return chrono

    def test_mark_finished_preserves_last_valid_result_after_invalid_attempt(self):
        existing = {"ok": True, "recommendation": {"frequency_hz": 50000}}
        invalid = {"ok": False, "recommendation": None, "diagnostics": {"valid_candidates": 0}}
        chrono = self.make_chronometer(existing)

        chrono._mark_calibration_finished(result=invalid)

        self.assertIs(chrono._calibration_status["last_attempt"], invalid)
        self.assertIs(chrono._calibration_status["last_result"], existing)
        self.assertIs(chrono._calibration_last_result, existing)

    def test_load_ignores_saved_calibration_above_timeout_ceiling(self):
        unsafe = {
            "ok": True,
            "recommendation": {
                "frequency_hz": 39000,
                "sensor_signal_timeout": 0.360,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ir_calibration.json"
            path.write_text(json.dumps(unsafe), encoding="utf-8")
            chrono = object.__new__(Chronometer)
            chrono._calibration_store_path = path

            self.assertIsNone(chrono._load_ir_calibration_file())

    def test_load_keeps_saved_calibration_with_safe_timeout(self):
        valid = {
            "ok": True,
            "recommendation": {
                "frequency_hz": 50000,
                "sensor_signal_timeout": 0.060,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ir_calibration.json"
            path.write_text(json.dumps(valid), encoding="utf-8")
            chrono = object.__new__(Chronometer)
            chrono._calibration_store_path = path

            self.assertEqual(chrono._load_ir_calibration_file(), valid)

    def test_calibration_does_not_apply_or_save_invalid_diagnostics(self):
        existing = {"ok": True, "recommendation": {"frequency_hz": 50000}}
        invalid = {"ok": False, "recommendation": None, "diagnostics": {"valid_candidates": 0}}
        chrono = self.make_chronometer(existing)
        chrono._estado = "idle"
        chrono.ir_pin = 17
        chrono.ir_led_pin = 18
        chrono.ir_duty_cycle = 50.0
        chrono.sensor_poll_interval = 0.001
        chrono.ir_pwm_backend = "fake"
        chrono.ir_pwm_chip = 0
        chrono.ir_pwm_channel = 0
        chrono.ir_burst_on_time = 0.006
        chrono.ir_burst_off_time = 0.014
        chrono.sensor_signal_timeout = 0.120
        chrono._stop_sensor_input = lambda: None
        chrono._stop_ir_emitter = lambda: None
        chrono._restart_gpio_processing = lambda: None
        mutations = []
        chrono._apply_ir_recommendation = lambda *_args, **_kwargs: mutations.append("applied")
        chrono._save_ir_calibration_file = lambda *_args, **_kwargs: mutations.append("saved")
        chrono.get_ir_config_status = lambda: chrono._calibration_status

        with (
            patch("app.chrono.GPIO", object()),
            patch("app.chrono.run_ir_calibration", return_value=invalid),
        ):
            status = chrono.calibrate_ir_sensor(apply=True, save=True, trigger="test")

        self.assertEqual(mutations, [])
        self.assertIs(status["last_attempt"], invalid)
        self.assertIs(status["last_result"], existing)
        self.assertIs(chrono._calibration_last_result, existing)
