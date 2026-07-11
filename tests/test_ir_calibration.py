import threading
import time
import unittest
from unittest.mock import call, patch

import app.ir_calibration as calibration
from app.ir_calibration import (
    BurstEnvelope,
    CalibrationError,
    Emitter,
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
    BCM = 11
    IN = 1
    PUD_UP = 22

    @staticmethod
    def setwarnings(_enabled):
        pass

    @staticmethod
    def setmode(_mode):
        pass

    @staticmethod
    def setup(_pin, _mode, pull_up_down=None):
        pass


class PipelineEmitter:
    def __init__(self, events):
        self.events = events
        self.duty = 50.0
        self.active = False
        self.active_backend = "fake.PWM"

    def setup(self):
        self.events.append(("setup",))

    def set_duty(self, duty):
        self.duty = float(duty)
        self.events.append(("duty", self.duty))

    def set_frequency(self, frequency):
        self.active = True
        self.events.append(("on", int(frequency), self.duty))

    def off(self):
        self.active = False
        self.events.append(("off",))

    def cleanup(self):
        self.active = False
        self.events.append(("cleanup",))


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


class FaultyEmitter(FakeEmitter):
    def __init__(self):
        super().__init__()
        self.frequency_attempted = threading.Event()
        self.failure = RuntimeError("carrier start failed")

    def set_frequency(self, frequency):
        self.events.append(("on_failed", frequency, self.duty))
        self.frequency_attempted.set()
        raise self.failure


class DelayedEmitter(FakeEmitter):
    def __init__(self):
        super().__init__()
        self.activation_entered = threading.Event()
        self.release_activation = threading.Event()

    def set_frequency(self, frequency):
        self.activation_entered.set()
        if not self.release_activation.wait(1.0):
            raise RuntimeError("carrier activation was not released")
        self.events.append(("on", frequency, self.duty))


class FakePWM:
    def __init__(self, duty_reader):
        self.duty_reader = duty_reader
        self.events = []

    def ChangeDutyCycle(self, duty):
        self.events.append(("duty", duty, self.duty_reader()))


class FakeEmitterGPIO:
    LOW = 0

    def __init__(self):
        self.outputs = []

    def output(self, pin, level):
        self.outputs.append((pin, level))


class OperationalHelpersTest(unittest.TestCase):
    def test_burst_envelope_stops_with_emitter_off(self):
        emitter = FakeEmitter()
        envelope = BurstEnvelope(emitter, 50000, 0.001, 0.001)
        envelope.start()
        time.sleep(0.005)
        envelope.stop()
        self.assertFalse(envelope.is_alive())
        self.assertEqual(emitter.events[-1], ("off",))

    def test_burst_stop_serializes_off_after_delayed_carrier_activation(self):
        emitter = DelayedEmitter()
        envelope = BurstEnvelope(emitter, 50000, 0.001, 0.001)
        envelope.start()
        self.assertTrue(emitter.activation_entered.wait(0.5))

        join_called = threading.Event()
        original_join = envelope._thread.join

        def recording_join(timeout=None):
            join_called.set()
            return original_join(timeout)

        envelope._thread.join = recording_join
        stop_returned = threading.Event()
        stop_errors = []

        def stop_envelope():
            try:
                envelope.stop()
            except Exception as exc:
                stop_errors.append(exc)
            finally:
                stop_returned.set()

        stop_thread = threading.Thread(target=stop_envelope)
        stop_thread.start()
        self.assertTrue(envelope._stop.wait(0.5))

        stop_returned_while_blocked = stop_returned.is_set()
        join_called_while_blocked = join_called.wait(0.1)
        emitter.release_activation.set()

        self.assertTrue(stop_returned.wait(0.5))
        stop_thread.join(timeout=0.5)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(stop_returned_while_blocked)
        self.assertFalse(join_called_while_blocked)
        self.assertEqual(stop_errors, [])
        self.assertFalse(envelope.is_alive())
        self.assertEqual(emitter.events[-1], ("off",))
        self.assertNotIn(("on", 50000, 50.0), emitter.events[-1:])

    def test_burst_stop_consumes_worker_error_after_first_raise(self):
        emitter = FaultyEmitter()
        envelope = BurstEnvelope(emitter, 50000, 0.001, 0.001)
        envelope.start()
        self.assertTrue(emitter.frequency_attempted.wait(0.5))

        with self.assertRaises(CalibrationError) as captured:
            envelope.stop()

        self.assertIs(captured.exception.__cause__, emitter.failure)
        envelope.stop()
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
        self.assertEqual(
            [item["duty"] for item in result["results"]],
            [50.0, 35.0, 20.0, 10.0],
        )
        self.assertEqual(emitter.duty, 50.0)

    def test_real_emitter_set_duty_turns_carrier_off_before_clamping(self):
        GPIO = FakeEmitterGPIO()
        emitter = Emitter(GPIO, 18, 50.0, pwm_backend="rpi_gpio")
        pwm = FakePWM(lambda: emitter.duty)
        emitter.pwm = pwm

        emitter.set_duty(125.0)
        emitter.set_duty(-5.0)

        self.assertEqual(
            pwm.events,
            [
                ("duty", 0, 50.0),
                ("duty", 0, 100.0),
            ],
        )
        self.assertEqual(GPIO.outputs, [(18, 0), (18, 0)])
        self.assertEqual(emitter.duty, 0.0)

    def test_margin_reader_failure_restores_requested_duty_and_turns_emitter_off(self):
        emitter = FakeEmitter()
        noise = summarize_samples(FakeGPIO, [0] * 10, 0.001, 0.002)

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            raise RuntimeError("reader failed")

        with self.assertRaisesRegex(RuntimeError, "reader failed"):
            run_margin_test(
                FakeGPIO,
                17,
                emitter,
                {"freq": 50000, "signal_level": FakeGPIO.HIGH},
                noise,
                0.01,
                0.001,
                0,
                0,
                25.0,
                0.002,
                window_reader=reader,
            )

        self.assertEqual(emitter.duty, 50.0)
        self.assertEqual(emitter.events[-1], ("off",))

    def test_margin_rejects_changed_signal_level(self):
        emitter = FakeEmitter()
        noise = summarize_samples(FakeGPIO, [1] * 10, 0.001, 0.002)

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            return summarize_samples(GPIO, [0] * 10, interval, confirm_time)

        result = run_margin_test(
            FakeGPIO,
            17,
            emitter,
            {"freq": 50000, "signal_level": FakeGPIO.HIGH},
            noise,
            0.01,
            0.001,
            0,
            0,
            25.0,
            0.002,
            window_reader=reader,
        )

        self.assertIsNone(result["minimum_stable_duty"])
        self.assertEqual(
            [item["reasons"] for item in result["results"]],
            [["signal_level_changed"]] * 4,
        )

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

    def test_operational_test_propagates_worker_failure_after_emitter_off(self):
        emitter = FaultyEmitter()

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            self.assertTrue(emitter.frequency_attempted.wait(0.5))
            return summarize_samples(GPIO, [1] * 100, interval, confirm_time)

        with self.assertRaises(CalibrationError) as captured:
            run_operational_test(
                FakeGPIO,
                17,
                emitter,
                {"freq": 50000, "signal_level": FakeGPIO.HIGH},
                0.006,
                0.014,
                0.1,
                0.1,
                0.1,
                0.001,
                0.002,
                0.120,
                settle=0,
                window_reader=reader,
            )

        self.assertIs(captured.exception.__cause__, emitter.failure)
        self.assertEqual(emitter.events[-1], ("off",))

    def test_break_release_ignores_short_pulse_before_qualifying_run(self):
        emitter = FakeEmitter()
        windows = iter([
            [1] * 6 + [0] * 14 + [1] * 6,
            [0] * 10 + [1] * 10 + [0] * 70,
            [0] * 5 + [1] * 50,
        ])

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            return summarize_samples(GPIO, next(windows), interval, confirm_time)

        result = run_operational_test(
            FakeGPIO,
            17,
            emitter,
            {"freq": 50000, "signal_level": FakeGPIO.HIGH},
            0.006,
            0.014,
            0.026,
            0.090,
            0.055,
            0.001,
            0.002,
            0.120,
            settle=0,
            window_reader=reader,
        )

        self.assertTrue(result["break_detected"])
        self.assertEqual(result["signal_timeout"], 0.06)
        self.assertEqual(result["break_release_s"], 0.02)

    def test_operational_reader_uses_requested_durations_and_break_timeout(self):
        emitter = FakeEmitter()
        calls = []
        windows = iter([
            [1] * 6 + [0] * 14 + [1] * 6,
            [0] * 250,
            [0] * 5 + [1] * 50,
        ])

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            calls.append((duration, confirm_time))
            return summarize_samples(GPIO, next(windows), interval, confirm_time)

        run_operational_test(
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

        self.assertEqual(
            calls,
            [
                (0.026, 0.002),
                (0.250, 0.06),
                (0.055, 0.002),
            ],
        )

    def test_reacquisition_sampling_does_not_wait_for_settle(self):
        emitter = FakeEmitter()
        windows = iter([
            summarize_samples(FakeGPIO, [1] * 6 + [0] * 14 + [1] * 6, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 250, 0.001, 0.06),
            summarize_samples(FakeGPIO, [0] * 5 + [1] * 50, 0.001, 0.002),
        ])

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            return next(windows)

        with patch("app.ir_calibration.time.sleep") as sleep:
            run_operational_test(
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
                settle=0.05,
                window_reader=reader,
            )

        self.assertEqual(sleep.call_args_list, [call(0.05)])

    def test_operational_test_orders_active_break_and_reacquisition_phases(self):
        events = []

        class RecordingEnvelope:
            def start(self):
                events.append("start")

            def stop(self):
                events.append("stop")

        windows = iter([
            summarize_samples(FakeGPIO, [1] * 6 + [0] * 14 + [1] * 6, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 250, 0.001, 0.06),
            summarize_samples(FakeGPIO, [0] * 5 + [1] * 50, 0.001, 0.002),
        ])
        read_phases = iter(["read_active", "read_break", "read_reacquire"])

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            events.append(next(read_phases))
            return next(windows)

        with patch(
            "app.ir_calibration.BurstEnvelope",
            return_value=RecordingEnvelope(),
        ):
            run_operational_test(
                FakeGPIO,
                17,
                FakeEmitter(),
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

        self.assertEqual(
            events,
            [
                "start",
                "read_active",
                "stop",
                "read_break",
                "start",
                "read_reacquire",
                "stop",
                "stop",
            ],
        )


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
        self.assertEqual(rejected[0]["reasons"], ["insufficient_contrast"])

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


class CalibrationPipelineTest(unittest.TestCase):
    @staticmethod
    def candidate_result(freq, minimum_stable_duty):
        return {
            "scan": {"freq": freq, "delta": 100.0, "signal_pct": 100.0},
            "margin": {"minimum_stable_duty": minimum_stable_duty},
            "burst": {"max_signal_gap": 0.020},
            "break_test": {
                "break_detected": True,
                "break_release_s": 0.0,
                "reacquire_s": 0.005,
            },
            "signal_timeout": 0.060,
            "valid": True,
            "reasons": [],
        }

    @staticmethod
    def shortlist_candidate(freq, hold_stability, contrast, signal_pct):
        return {
            "scan": {
                "freq": freq,
                "delta": contrast,
                "signal_pct": signal_pct,
            },
            "hold": {"expected_pct": hold_stability},
        }

    def sorted_shortlist(self, candidates, preferred_frequency, preference_tolerance):
        return sorted(
            candidates,
            key=lambda candidate: calibration._shortlist_key(
                candidate,
                preferred_frequency,
                preference_tolerance,
            ),
        )

    def test_candidate_ranking_prefers_lower_stable_duty_before_frequency_preference(self):
        candidates = [
            self.candidate_result(50000, minimum_stable_duty=35.0),
            self.candidate_result(48000, minimum_stable_duty=20.0),
        ]

        selected = calibration.choose_operational_candidate(
            candidates,
            preferred_frequency=50000,
        )

        self.assertEqual(selected["scan"]["freq"], 48000)

    def test_shortlist_uses_frequency_preference_within_equivalent_tolerance_buckets(self):
        candidates = [
            self.shortlist_candidate(50000, 95.1, 80.1, 90.1),
            self.shortlist_candidate(48000, 95.4, 80.4, 90.4),
        ]

        selected = self.sorted_shortlist(candidates, 50000, 1.0)[0]

        self.assertEqual(selected["scan"]["freq"], 50000)

    def test_shortlist_keeps_materially_better_bucket_ahead_of_preferred_frequency(self):
        candidates = [
            self.shortlist_candidate(50000, 95.4, 80.4, 90.4),
            self.shortlist_candidate(48000, 96.1, 80.1, 90.1),
        ]

        selected = self.sorted_shortlist(candidates, 50000, 1.0)[0]

        self.assertEqual(selected["scan"]["freq"], 48000)

    def test_failed_attempt_is_not_persistable(self):
        self.assertFalse(
            calibration.calibration_result_is_valid({"ok": False, "recommendation": None})
        )

    def test_cli_accepts_multi_phase_timing_and_finalist_controls(self):
        args = calibration.build_arg_parser().parse_args([
            "--noise-confirm-time", "0.003",
            "--finalist-count", "3",
            "--burst-on", "0.007",
            "--burst-off", "0.015",
            "--burst-test-duration", "1.2",
            "--break-duration", "0.3",
            "--reacquire-duration", "0.6",
            "--max-signal-timeout", "0.1",
        ])

        self.assertEqual(args.noise_confirm_time, 0.003)
        self.assertEqual(args.finalist_count, 3)
        self.assertEqual(args.burst_on, 0.007)
        self.assertEqual(args.burst_off, 0.015)
        self.assertEqual(args.burst_test_duration, 1.2)
        self.assertEqual(args.break_duration, 0.3)
        self.assertEqual(args.reacquire_duration, 0.6)
        self.assertEqual(args.max_signal_timeout, 0.1)
        self.assertTrue(
            calibration.calibration_result_is_valid(
                {"ok": True, "recommendation": {"frequency_hz": 50000}}
            )
        )

    def test_pipeline_reads_all_off_windows_first_and_only_tests_clean_finalists(self):
        events = []
        emitter = PipelineEmitter(events)
        frequencies = [38000, 40000, 42000, 50000]
        noise_stats = [
            summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0, 1, 1, 1, 0], 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
        ]
        active_stats = [
            summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002),
            summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002),
            summarize_samples(FakeGPIO, [1, 0, 0, 0, 0], 0.001, 0.002),
            summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002),
        ]
        windows = iter(noise_stats + active_stats)
        margin_frequencies = []
        operational_frequencies = []
        progress_phases = []

        def window_reader(*_args, **_kwargs):
            events.append(("read_active" if emitter.active else "read_off",))
            return next(windows)

        def hold_reader(_GPIO, _pin, _level, _duration, _interval, _gap):
            frequency = events[-1][1] if events[-1][0] == "on" else None
            saturated = frequency == 38000
            return {
                "samples": 5,
                "expected_pct": 100.0,
                "expected_samples": 5,
                "high": 5,
                "low": 0,
                "high_pct": 100.0,
                "low_pct": 0.0,
                "transitions": 0,
                "first": 1,
                "last": 1,
                "first_expected_at": 0.0,
                "last_expected_at": 0.004,
                "lost_after": 0.001 if saturated else None,
                "saturated": saturated,
            }

        def margin_runner(_GPIO, _pin, _emitter, scan, *_args, **_kwargs):
            margin_frequencies.append(scan["freq"])
            return {
                "requested_duty": 50.0,
                "minimum_stable_duty": 20.0,
                "results": [{"duty": 20.0, "valid": True}],
            }

        def operational_runner(_GPIO, _pin, _emitter, scan, *_args, **_kwargs):
            operational_frequencies.append(scan["freq"])
            _kwargs["progress"]({"phase": "break_test", "frequency_hz": scan["freq"]})
            return self.operational_result()

        with (
            patch("app.ir_calibration.Emitter", return_value=emitter),
            patch("app.ir_calibration.read_window", side_effect=window_reader),
            patch("app.ir_calibration.read_saturation_window", side_effect=hold_reader),
            patch("app.ir_calibration.run_margin_test", side_effect=margin_runner),
            patch("app.ir_calibration.run_operational_test", side_effect=operational_runner),
        ):
            result = calibration.run_ir_calibration(
                FakeGPIO,
                freqs=frequencies,
                duration=0,
                settle=0,
                recovery=0,
                hold_duration=0,
                progress=lambda update: progress_phases.append(update["phase"]),
            )

        first_active = next(index for index, event in enumerate(events) if event[0] == "on")
        self.assertEqual(
            [event for event in events[:first_active] if event[0].startswith("read_")],
            [("read_off",)] * len(frequencies),
        )
        self.assertEqual(margin_frequencies, [50000])
        self.assertEqual(operational_frequencies, [50000])
        self.assertEqual(result["recommendation"]["frequency_hz"], 50000)
        self.assertEqual(result["recommendation"]["burst_on"], 0.006)
        self.assertEqual(result["recommendation"]["burst_off"], 0.014)
        self.assertEqual(result["recommendation"]["sensor_signal_timeout"], 0.060)
        self.assertEqual(result["recommendation"]["minimum_stable_duty"], 20.0)
        ordered_phases = [
            "noise_scan",
            "active_scan",
            "hold",
            "margin_test",
            "burst_test",
            "break_test",
            "select",
        ]
        first_phase_positions = [progress_phases.index(phase) for phase in ordered_phases]
        self.assertEqual(first_phase_positions, sorted(first_phase_positions))
        self.assertTrue(
            {"noise_scan", "rejected", "margin", "burst", "break_tests", "diagnostics"}
            <= result.keys()
        )
        self.assertEqual(len(result["diagnostics"]["finalist_results"]), 1)
        self.assertFalse(result["diagnostics"]["physical_break_validated"])

    def test_clean_candidates_still_reach_operational_tests_when_hold_is_skipped(self):
        events = []
        emitter = PipelineEmitter(events)
        windows = iter([
            summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
            summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002),
        ])
        tested = []

        def margin_runner(_GPIO, _pin, _emitter, scan, *_args, **_kwargs):
            tested.append(("margin", scan["freq"]))
            return {
                "requested_duty": 50.0,
                "minimum_stable_duty": 20.0,
                "results": [{"duty": 20.0, "valid": True}],
            }

        def operational_runner(_GPIO, _pin, _emitter, scan, *_args, **_kwargs):
            tested.append(("operational", scan["freq"]))
            return self.operational_result()

        with (
            patch("app.ir_calibration.Emitter", return_value=emitter),
            patch("app.ir_calibration.read_window", side_effect=lambda *_args: next(windows)),
            patch("app.ir_calibration.read_saturation_window", side_effect=AssertionError("hold ran")),
            patch("app.ir_calibration.run_margin_test", side_effect=margin_runner),
            patch("app.ir_calibration.run_operational_test", side_effect=operational_runner),
        ):
            result = calibration.run_ir_calibration(
                FakeGPIO,
                freqs=[50000],
                duration=0,
                settle=0,
                recovery=0,
                skip_hold=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(tested, [("margin", 50000), ("operational", 50000)])

    def test_pipeline_caps_requested_finalists_at_five(self):
        events = []
        emitter = PipelineEmitter(events)
        frequencies = [44000, 45000, 46000, 47000, 48000, 49000, 50000]
        noise = [summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002) for _ in frequencies]
        active = [summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002) for _ in frequencies]
        windows = iter(noise + active)
        margin_frequencies = []
        operational_frequencies = []

        def margin_runner(_GPIO, _pin, _emitter, scan, *_args, **_kwargs):
            margin_frequencies.append(scan["freq"])
            return {
                "requested_duty": 50.0,
                "minimum_stable_duty": 20.0,
                "results": [{"duty": 20.0, "valid": True}],
            }

        def operational_runner(_GPIO, _pin, _emitter, scan, *_args, **_kwargs):
            operational_frequencies.append(scan["freq"])
            return self.operational_result()

        with (
            patch("app.ir_calibration.Emitter", return_value=emitter),
            patch("app.ir_calibration.read_window", side_effect=lambda *_args: next(windows)),
            patch("app.ir_calibration.read_saturation_window", side_effect=AssertionError("hold ran")),
            patch("app.ir_calibration.run_margin_test", side_effect=margin_runner),
            patch("app.ir_calibration.run_operational_test", side_effect=operational_runner),
        ):
            result = calibration.run_ir_calibration(
                FakeGPIO,
                freqs=frequencies,
                duration=0,
                settle=0,
                recovery=0,
                skip_hold=True,
                finalist_count=99,
            )

        self.assertEqual(result["options"]["finalist_count"], 5)
        self.assertEqual(len(margin_frequencies), 5)
        self.assertEqual(len(operational_frequencies), 5)
        self.assertEqual(len(result["margin"]), 5)
        self.assertEqual(len(result["burst"]), 5)

    def test_no_candidate_returns_diagnostics_without_recommendation(self):
        events = []
        emitter = PipelineEmitter(events)
        unchanged = summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002)

        with (
            patch("app.ir_calibration.Emitter", return_value=emitter),
            patch("app.ir_calibration.read_window", side_effect=[unchanged, unchanged]),
            patch("app.ir_calibration.run_margin_test", side_effect=AssertionError("margin ran")),
            patch("app.ir_calibration.run_operational_test", side_effect=AssertionError("operational ran")),
        ):
            result = calibration.run_ir_calibration(
                FakeGPIO,
                freqs=[50000],
                duration=0,
                settle=0,
                recovery=0,
            )

        self.assertFalse(result["ok"])
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["diagnostics"]["valid_candidates"], 0)

    @staticmethod
    def operational_result():
        active = summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002)
        broken = summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002)
        return {
            "active": active,
            "break": broken,
            "reacquire": active,
            "active_max_gap_s": 0.020,
            "signal_timeout": 0.060,
            "timeout_valid": True,
            "timeout_reason": None,
            "break_detected": True,
            "break_release_s": 0.0,
            "break_run_s": 0.250,
            "residual_signal_samples": 0,
            "reacquire_s": 0.005,
            "reacquired": True,
        }
