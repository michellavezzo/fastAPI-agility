import unittest

from app.ir_calibration import (
    calculate_signal_timeout,
    classify_frequency_results,
    margin_duty_values,
    minimum_stable_duty,
    summarize_samples,
)


class FakeGPIO:
    LOW = 0
    HIGH = 1


class CalibrationMetricsTest(unittest.TestCase):
    def test_summarize_samples_tracks_confirmed_runs(self):
        stats = summarize_samples(FakeGPIO, [0, 0, 1, 1, 1, 0], 0.001, 0.002)
        self.assertEqual(stats["max_high_run_s"], 0.003)
        self.assertEqual(stats["first_high_confirmed_at"], 0.002)

    def test_classification_rejects_confirmed_off_signal(self):
        noise = [(50000, summarize_samples(FakeGPIO, [0, 1, 1, 1, 0], 0.001, 0.002))]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )
        self.assertEqual(sensitive, [])
        self.assertEqual(rejected[0]["reasons"], ["noise_detected_off"])

    def test_classification_keeps_clean_high_contrast_signal(self):
        noise = [(50000, summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002))]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )
        self.assertEqual([item["freq"] for item in sensitive], [50000])
        self.assertEqual(rejected, [])

    def test_margin_ratios_preserve_requested_duty_order(self):
        self.assertEqual(margin_duty_values(50), [50.0, 35.0, 20.0, 10.0])

    def test_minimum_stable_duty_uses_lowest_valid_result(self):
        results = [
            {"duty": 50.0, "valid": True},
            {"duty": 35.0, "valid": True},
            {"duty": 20.0, "valid": False},
        ]
        self.assertEqual(minimum_stable_duty(results), 35.0)

    def test_stable_20ms_burst_recommends_60ms_timeout(self):
        result = calculate_signal_timeout(0.006, 0.014, 0.020)
        self.assertEqual(result["signal_timeout"], 0.06)
        self.assertTrue(result["valid"])

    def test_timeout_above_120ms_is_invalid(self):
        result = calculate_signal_timeout(0.006, 0.014, 0.070)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "signal_gap_too_large")
