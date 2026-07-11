import time
import unittest

from app.ir_calibration import (
    BurstEnvelope,
    CalibrationError,
    calculate_signal_timeout,
    classify_frequency_results,
    margin_duty_values,
    minimum_stable_duty,
    run_margin_test,
    run_operational_test,
    summarize_samples,
)


class FakeGPIO:
    LOW = 0
    HIGH = 1


class FakeEmitter:
    def __init__(self):
        self.duty = 50.0
        self.events = []

    def set_duty(self, duty):
        self.duty = duty
        self.events.append(("duty", duty))

    def set_frequency(self, frequency):
        self.events.append(("on", frequency, self.duty))

    def off(self):
        self.events.append(("off",))


class OperationalHelpersTest(unittest.TestCase):
    def test_burst_envelope_stops_with_emitter_off(self):
        emitter = FakeEmitter()
        envelope = BurstEnvelope(emitter, 50000, 0.001, 0.001)
        envelope.start()
        time.sleep(0.005)
        envelope.stop()
        self.assertFalse(envelope.is_alive())
        self.assertEqual(emitter.events[-1], ("off",))

    def test_margin_sweep_uses_lowest_stable_duty_and_restores_requested_duty(self):
        emitter = FakeEmitter()
        noise = summarize_samples(FakeGPIO, [0] * 10, 0.001, 0.002)
        scan = {"freq": 50000, "signal_level": FakeGPIO.HIGH}

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            samples = [1] * 10 if emitter.duty >= 35 else [0] * 10
            return summarize_samples(GPIO, samples, interval, confirm_time)

        result = run_margin_test(
            FakeGPIO,
            17,
            emitter,
            scan,
            noise,
            0.01,
            0.001,
            0,
            0,
            25.0,
            0.002,
            window_reader=reader,
        )

        self.assertEqual(result["minimum_stable_duty"], 35.0)
        self.assertEqual(emitter.duty, 50.0)

    def test_operational_test_detects_break_and_reacquisition(self):
        emitter = FakeEmitter()
        windows = iter([
            summarize_samples(FakeGPIO, [1] * 6 + [0] * 14 + [1] * 6, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 250, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 5 + [1] * 50, 0.001, 0.002),
        ])

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            return next(windows)

        result = run_operational_test(
            FakeGPIO,
            17,
            emitter,
            {"freq": 50000, "signal_level": FakeGPIO.HIGH},
            0.006,
            0.014,
            0.026,
            0.250,
            0.055,
            0.001,
            0.002,
            0.120,
            settle=0,
            window_reader=reader,
        )

        self.assertTrue(result["break_detected"])
        self.assertEqual(result["signal_timeout"], 0.06)
        self.assertEqual(result["break_release_s"], 0.0)
        self.assertEqual(result["reacquire_s"], 0.005)


class CalibrationMetricsTest(unittest.TestCase):
    def test_summarize_samples_tracks_confirmed_runs(self):
        stats = summarize_samples(FakeGPIO, [0, 0, 1, 1, 1, 0], 0.001, 0.002)
        self.assertEqual(stats["max_high_run_s"], 0.003)
        self.assertEqual(stats["first_high_confirmed_at"], 0.002)

    def test_classification_rejects_confirmed_off_signal(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0, 1, 1, 1, 0], 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )
        self.assertEqual(sensitive, [])
        self.assertEqual(rejected[0]["reasons"], ["noise_detected_off"])

    def test_classification_keeps_clean_high_contrast_signal(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )
        self.assertEqual([item["freq"] for item in sensitive], [50000])
        self.assertEqual(rejected, [])

    def test_contrast_floor_rejects_delta_below_25_percent(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1, 0, 0, 0, 0], 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 5.0, 0.002
        )
        self.assertEqual(sensitive, [])
        self.assertEqual(rejected[0]["reasons"], ["insufficient_delta"])

    def test_noise_confirmation_floor_ignores_one_millisecond_run(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0, 1, 0, 0, 0], 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.001
        )
        self.assertEqual([item["freq"] for item in sensitive], [50000])
        self.assertEqual(rejected, [])

    def test_classification_requires_equal_window_counts(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
        }]
        active = [
            (50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002)),
            (51000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002)),
        ]
        with self.assertRaises(CalibrationError):
            classify_frequency_results(FakeGPIO, noise, active, 25.0, 0.002)

    def test_classification_pairs_off_window_by_temporal_position(self):
        noise = [{
            "window_index": 7,
            "candidate_frequency_hz": 38000,
            "stats": summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]

        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )

        self.assertEqual(rejected, [])
        self.assertEqual(sensitive[0]["freq"], 50000)
        self.assertEqual(sensitive[0]["noise_window_index"], 7)
        self.assertEqual(sensitive[0]["candidate_frequency_hz"], 38000)

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

    def test_timeout_ceiling_cannot_be_relaxed_above_120ms(self):
        result = calculate_signal_timeout(0.006, 0.014, 0.070, max_timeout=0.2)
        self.assertEqual(result["signal_timeout"], 0.145)
        self.assertFalse(result["valid"])
