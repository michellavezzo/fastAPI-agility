import threading
import time
import unittest
from unittest.mock import call, patch

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
