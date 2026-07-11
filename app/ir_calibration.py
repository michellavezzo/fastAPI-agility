import argparse
import json
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


SYSTEM_DIST_PACKAGES = (
    "/usr/lib/python3/dist-packages",
    "/usr/local/lib/python3/dist-packages",
)

HARDWARE_PWM_PINS = {12, 13, 18, 19}
KERNEL_PWM_CHANNEL_BY_PIN = {
    12: 0,
    13: 1,
    18: 0,
    19: 1,
}


class CalibrationError(RuntimeError):
    pass


def _append_system_dist_packages():
    for path in SYSTEM_DIST_PACKAGES:
        if path not in sys.path:
            sys.path.append(path)


def load_gpio():
    try:
        import RPi.GPIO as GPIO
        return GPIO
    except Exception as exc:
        first_error = exc

    _append_system_dist_packages()
    try:
        import RPi.GPIO as GPIO
        return GPIO
    except Exception:
        pass

    raise CalibrationError(
        f"nao foi possivel importar RPi.GPIO: {type(first_error).__name__}: {first_error}"
    )


def load_pigpio_client():
    try:
        import pigpio
    except Exception:
        _append_system_dist_packages()
        try:
            import pigpio
        except Exception as exc:
            return None, None, f"{type(exc).__name__}: {exc}"

    pi = pigpio.pi()
    if not pi.connected:
        return pigpio, None, "pigpio importado, mas daemon pigpiod nao conectado"
    return pigpio, pi, None


def normalize_pwm_backend(backend):
    value = str(backend or "auto").strip().lower()
    if value in ("rpi", "rpi.gpio", "gpio", "software"):
        return "rpi_gpio"
    if value in ("kernel", "linux", "sysfs", "hardware", "hardware_pwm"):
        return "kernel_pwm"
    if value not in ("auto", "kernel_pwm", "pigpio", "rpi_gpio"):
        return "auto"
    return value


def kernel_pwm_channel_for_pin(pin, channel=None):
    if channel is not None:
        return int(channel)
    pin = int(pin)
    if pin not in KERNEL_PWM_CHANNEL_BY_PIN:
        raise CalibrationError(
            f"GPIO{pin} nao tem canal PWM do kernel mapeado automaticamente; "
            "use GPIO18/GPIO19 ou configure AGILITY_IR_PWM_CHANNEL."
        )
    return KERNEL_PWM_CHANNEL_BY_PIN[pin]


class KernelSysfsPWM:
    def __init__(self, pin, chip=0, channel=None):
        self.pin = int(pin)
        self.chip = int(chip)
        self.channel = kernel_pwm_channel_for_pin(self.pin, channel)
        self.chip_path = Path("/sys/class/pwm") / f"pwmchip{self.chip}"
        self.pwm_path = self.chip_path / f"pwm{self.channel}"
        self.frequency = None
        self.duty_cycle = 0.0
        self.enabled = False

    def setup(self):
        if not self.chip_path.is_dir():
            raise CalibrationError(
                f"PWM do kernel indisponivel em {self.chip_path}. "
                "Habilite dtoverlay=pwm-2chan em /boot/firmware/config.txt e reinicie."
            )

        if not self.pwm_path.is_dir():
            self._write(self.chip_path / "export", self.channel)
            self._wait_ready()
        else:
            self._wait_ready()

        self.off()

    def start(self, frequency, duty_cycle):
        frequency = int(frequency)
        duty_cycle = max(0.0, min(100.0, float(duty_cycle)))

        if self.frequency != frequency:
            period_ns = self._period_ns(frequency)
            self._write(self.pwm_path / "duty_cycle", 0)
            self._write(self.pwm_path / "period", period_ns)
            self.frequency = frequency

        duty_ns = int(self._period_ns(frequency) * duty_cycle / 100)
        self._write(self.pwm_path / "duty_cycle", duty_ns)
        self._write(self.pwm_path / "enable", 1)
        self.duty_cycle = duty_cycle
        self.enabled = True

    def off(self):
        if not self.pwm_path.is_dir():
            return
        self._write(self.pwm_path / "duty_cycle", 0)
        self._write(self.pwm_path / "enable", 0)
        self.duty_cycle = 0.0
        self.enabled = False

    def cleanup(self):
        self.off()

    @staticmethod
    def _period_ns(frequency):
        if frequency <= 0:
            raise CalibrationError("frequencia PWM do kernel precisa ser maior que zero")
        return int(1_000_000_000 / frequency)

    def _wait_ready(self, timeout=2.0):
        deadline = time.perf_counter() + timeout
        required = ("period", "duty_cycle", "enable")
        while time.perf_counter() < deadline:
            if self.pwm_path.is_dir() and all((self.pwm_path / name).exists() for name in required):
                return
            time.sleep(0.01)
        raise CalibrationError(f"PWM do kernel nao ficou pronto em {self.pwm_path}")

    @staticmethod
    def _write(path, value):
        try:
            with Path(path).open("w", encoding="ascii") as fp:
                fp.write(f"{value}\n")
        except PermissionError as exc:
            raise CalibrationError(
                f"sem permissao para escrever em {path}; execute como root ou ajuste permissoes do pwmchip"
            ) from exc
        except OSError as exc:
            raise CalibrationError(f"falha ao escrever {value!r} em {path}: {exc}") from exc


def level_name(GPIO, level):
    if level == GPIO.HIGH:
        return "HIGH"
    if level == GPIO.LOW:
        return "LOW"
    return str(level)


def opposite_level(GPIO, level):
    return GPIO.LOW if level == GPIO.HIGH else GPIO.HIGH


def level_pct(GPIO, stats, level):
    return stats["high_pct"] if level == GPIO.HIGH else stats["low_pct"]


def frequency_list(start, stop, step, freqs=None):
    if freqs:
        if isinstance(freqs, str):
            return [int(part.strip()) for part in freqs.split(",") if part.strip()]
        return [int(freq) for freq in freqs]
    return list(range(int(start), int(stop) + 1, int(step)))


def summarize_samples(GPIO, samples, interval, confirm_time=0.002):
    samples = list(samples)
    high = sum(level == GPIO.HIGH for level in samples)
    low = len(samples) - high
    transitions = sum(left != right for left, right in zip(samples, samples[1:]))

    def run_metrics(level):
        longest = 0
        current = 0
        first_confirmed_at = None
        required = max(1, math.ceil(confirm_time / interval))
        for index, sample in enumerate(samples):
            current = current + 1 if sample == level else 0
            longest = max(longest, current)
            if current >= required and first_confirmed_at is None:
                first_confirmed_at = (index - current + 1) * interval
        return round(longest * interval, 6), (
            round(first_confirmed_at, 6) if first_confirmed_at is not None else None
        )

    max_high, first_high = run_metrics(GPIO.HIGH)
    max_low, first_low = run_metrics(GPIO.LOW)
    count = len(samples)
    return {
        "samples": count,
        "high": high,
        "low": low,
        "high_pct": round(high / count * 100, 3) if count else 0,
        "low_pct": round(low / count * 100, 3) if count else 0,
        "transitions": transitions,
        "first": samples[0] if samples else None,
        "last": samples[-1] if samples else None,
        "max_high_run_s": max_high,
        "max_low_run_s": max_low,
        "first_high_confirmed_at": first_high,
        "first_low_confirmed_at": first_low,
    }


def read_window(GPIO, sensor_pin, duration, interval, confirm_time=0.002):
    end = time.perf_counter() + duration
    samples = []

    while time.perf_counter() < end:
        samples.append(GPIO.input(sensor_pin))
        time.sleep(interval)

    return summarize_samples(GPIO, samples, interval, confirm_time)


class Emitter:
    def __init__(self, GPIO, pin, duty, pwm_backend="auto", pwm_chip=0, pwm_channel=None):
        self.GPIO = GPIO
        self.pin = int(pin)
        self.duty = max(0.0, min(100.0, float(duty)))
        self.pwm_backend = normalize_pwm_backend(pwm_backend)
        self.pwm_chip = int(pwm_chip)
        self.pwm_channel = pwm_channel
        self.active_backend = None
        self.pwm = None
        self.kernel_pwm = None
        self.pigpio = None
        self.pi = None

    def setup(self):
        if self.pwm_backend in ("auto", "kernel_pwm"):
            try:
                self._setup_kernel_pwm()
                return
            except CalibrationError:
                if self.pwm_backend == "kernel_pwm":
                    raise
            except Exception as exc:
                if self.pwm_backend == "kernel_pwm":
                    raise CalibrationError(f"falha ao iniciar PWM do kernel: {exc}") from exc

        if self.pwm_backend in ("auto", "pigpio"):
            try:
                self._setup_pigpio()
                return
            except CalibrationError:
                if self.pwm_backend == "pigpio":
                    raise
            except Exception as exc:
                if self.pwm_backend == "pigpio":
                    raise CalibrationError(f"falha ao iniciar pigpio.hardware_PWM: {exc}") from exc

        self.GPIO.setup(self.pin, self.GPIO.OUT, initial=self.GPIO.LOW)
        self.pwm = self.GPIO.PWM(self.pin, 38000)
        self.pwm.start(0)
        self.active_backend = "RPi.GPIO.PWM"

    def _setup_kernel_pwm(self):
        if self.pin not in HARDWARE_PWM_PINS:
            raise CalibrationError(
                f"GPIO{self.pin} nao suporta PWM de hardware; use {sorted(HARDWARE_PWM_PINS)}"
            )

        self.kernel_pwm = KernelSysfsPWM(
            self.pin,
            chip=self.pwm_chip,
            channel=self.pwm_channel,
        )
        self.kernel_pwm.setup()
        self.active_backend = "kernel.sysfs.PWM"

    def _setup_pigpio(self):
        if self.pin not in HARDWARE_PWM_PINS:
            raise CalibrationError(
                f"GPIO{self.pin} nao suporta pigpio.hardware_PWM; use um destes GPIO BCM: "
                f"{sorted(HARDWARE_PWM_PINS)}"
            )

        pigpio, pi, error = load_pigpio_client()
        if error:
            raise CalibrationError(error)

        self.pigpio = pigpio
        self.pi = pi
        self.pi.set_mode(self.pin, self.pigpio.OUTPUT)
        self.active_backend = "pigpio.hardware_PWM"

    def set_frequency(self, frequency):
        frequency = int(frequency)
        if self.kernel_pwm is not None:
            self.kernel_pwm.start(frequency, self.duty)
            return
        if self.pi is not None:
            self.pi.hardware_PWM(self.pin, frequency, int(self.duty * 10000))
            return

        self.pwm.ChangeFrequency(frequency)
        self.pwm.ChangeDutyCycle(self.duty)

    def set_duty(self, duty):
        self.off()
        self.duty = max(0.0, min(100.0, float(duty)))

    def off(self):
        if self.kernel_pwm is not None:
            self.kernel_pwm.off()
            return
        if self.pi is not None:
            self.pi.hardware_PWM(self.pin, 0, 0)
            return
        if self.pwm is not None:
            self.pwm.ChangeDutyCycle(0)
        self.GPIO.output(self.pin, self.GPIO.LOW)

    def cleanup(self):
        try:
            self.off()
        finally:
            if self.pwm is not None:
                self.pwm.stop()
                self.pwm = None
            if self.kernel_pwm is not None:
                self.kernel_pwm.cleanup()
                self.kernel_pwm = None
            if self.pi is not None:
                self.pi.stop()
                self.pi = None


class BurstEnvelope:
    def __init__(self, emitter, frequency, burst_on, burst_off):
        self.emitter = emitter
        self.frequency = int(frequency)
        self.burst_on = max(0.0005, float(burst_on))
        self.burst_off = max(0.0005, float(burst_off))
        self._stop = threading.Event()
        self._carrier_lock = threading.Lock()
        self._thread = None
        self._error = None

    def start(self):
        if self.is_alive():
            return
        self._error = None
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="agility-ir-calibration-burst",
            daemon=True,
        )
        self._thread.start()

    def _run(self):
        error = None
        try:
            while not self._stop.is_set():
                with self._carrier_lock:
                    if self._stop.is_set():
                        break
                    self.emitter.set_frequency(self.frequency)
                if self._stop.wait(self.burst_on):
                    break
                with self._carrier_lock:
                    self.emitter.off()
                if self._stop.wait(self.burst_off):
                    break
        except Exception as exc:
            error = exc
        finally:
            try:
                with self._carrier_lock:
                    self.emitter.off()
            except Exception as exc:
                if error is None:
                    error = exc
            self._error = error

    def stop(self):
        self._stop.set()
        stop_error = None
        try:
            with self._carrier_lock:
                self.emitter.off()
        except Exception as exc:
            stop_error = exc
        # OFF under the lock plus the worker's locked event check forbids any later ON.
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, (self.burst_on + self.burst_off) * 4))
        if self.is_alive():
            raise CalibrationError("thread do envelope de burst nao encerrou")
        error, self._error = self._error, None
        if error is not None:
            raise CalibrationError(f"falha na thread do envelope IR: {error}") from error
        if stop_error is not None:
            raise CalibrationError(f"falha ao desligar envelope IR: {stop_error}") from stop_error

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())


def read_burst_window(
    GPIO,
    sensor_pin,
    emitter,
    frequency,
    burst_on,
    burst_off,
    duration,
    interval,
    confirm_time,
    settle=0.05,
    window_reader=read_window,
):
    envelope = BurstEnvelope(emitter, frequency, burst_on, burst_off)
    try:
        envelope.start()
        time.sleep(settle)
        return window_reader(GPIO, sensor_pin, duration, interval, confirm_time)
    finally:
        envelope.stop()


def score_frequency(GPIO, baseline, freq, stats):
    high_delta = stats["high_pct"] - baseline["high_pct"]
    low_delta = stats["low_pct"] - baseline["low_pct"]
    if high_delta >= low_delta:
        signal_level = GPIO.HIGH
        delta = high_delta
    else:
        signal_level = GPIO.LOW
        delta = low_delta

    return {
        "freq": int(freq),
        "stats": stats,
        "signal_level": int(signal_level),
        "signal_level_name": level_name(GPIO, signal_level),
        "signal_pct": round(level_pct(GPIO, stats, signal_level), 3),
        "delta": round(delta, 3),
    }


def evaluate_frequency(GPIO, noise_stats, freq, active_stats, min_delta, noise_confirm_time):
    scored = score_frequency(GPIO, noise_stats, freq, active_stats)
    signal_level = scored["signal_level"]
    effective_min_delta = max(float(min_delta), 25.0)
    effective_noise_confirm_time = max(float(noise_confirm_time), 0.002)
    noise_signal_pct = level_pct(GPIO, noise_stats, signal_level)
    noise_longest_run_s = (
        noise_stats["max_high_run_s"]
        if signal_level == GPIO.HIGH
        else noise_stats["max_low_run_s"]
    )
    reasons = []
    if scored["delta"] < effective_min_delta:
        reasons.append("insufficient_contrast")
    if noise_longest_run_s >= effective_noise_confirm_time:
        reasons.append("noise_detected_off")

    return {
        **scored,
        "valid": not reasons,
        "noise_stats": noise_stats,
        "noise_signal_pct": noise_signal_pct,
        "noise_longest_run_s": noise_longest_run_s,
        "reasons": reasons,
    }


def classify_frequency_results(GPIO, noise_results, active_results, min_delta, noise_confirm_time):
    if len(noise_results) != len(active_results):
        raise CalibrationError("quantidades de janelas OFF e ativas precisam ser iguais")

    sensitive = []
    rejected = []
    for noise_window, (freq, active_stats) in zip(noise_results, active_results):
        result = evaluate_frequency(
            GPIO,
            noise_window["stats"],
            freq,
            active_stats,
            min_delta,
            noise_confirm_time,
        )
        result["noise_window_index"] = int(noise_window["window_index"])
        result["candidate_frequency_hz"] = int(noise_window["candidate_frequency_hz"])
        if result["valid"]:
            sensitive.append(result)
        else:
            rejected.append(result)
    return sensitive, rejected


def margin_duty_values(duty):
    values = [round(max(0.0, min(100.0, float(duty) * ratio)), 3) for ratio in (1.0, 0.7, 0.4, 0.2)]
    return list(dict.fromkeys(values))


def minimum_stable_duty(results):
    stable = [float(item["duty"]) for item in results if item.get("valid")]
    return min(stable, default=None)


def calculate_signal_timeout(burst_on, burst_off, max_signal_gap, max_timeout=0.12):
    period = float(burst_on) + float(burst_off)
    timeout = round(max(period * 3, float(max_signal_gap) * 2 + 0.005), 6)
    effective_max_timeout = min(float(max_timeout), 0.120)
    valid = timeout <= effective_max_timeout
    return {
        "signal_timeout": timeout,
        "valid": valid,
        "reason": None if valid else "signal_gap_too_large",
    }


def run_margin_test(
    GPIO,
    sensor_pin,
    emitter,
    scan,
    noise_stats,
    duration,
    interval,
    settle,
    recovery,
    min_delta,
    confirm_time,
    burst_on=0.006,
    burst_off=0.014,
    window_reader=read_window,
):
    requested_duty = float(emitter.duty)
    results = []
    frequency = int(scan["freq"])
    expected_signal_level = int(scan["signal_level"])

    try:
        for duty in margin_duty_values(requested_duty):
            emitter.off()
            time.sleep(recovery)
            emitter.set_duty(duty)
            stats = read_burst_window(
                GPIO,
                sensor_pin,
                emitter,
                frequency,
                burst_on,
                burst_off,
                duration,
                interval,
                confirm_time,
                settle=settle,
                window_reader=window_reader,
            )
            result = evaluate_frequency(
                GPIO,
                noise_stats,
                frequency,
                stats,
                min_delta,
                confirm_time,
            )
            result["duty"] = duty
            result["expected_signal_level"] = expected_signal_level
            if result["signal_level"] != expected_signal_level:
                result["valid"] = False
                result["reasons"].append("signal_level_changed")
            results.append(result)
            emitter.off()
    finally:
        try:
            emitter.set_duty(requested_duty)
        finally:
            emitter.off()

    return {
        "requested_duty": requested_duty,
        "results": results,
        "minimum_stable_duty": minimum_stable_duty(results),
    }


def run_operational_test(
    GPIO,
    sensor_pin,
    emitter,
    scan,
    burst_on,
    burst_off,
    active_duration,
    break_duration,
    reacquire_duration,
    interval,
    confirm_time,
    max_signal_timeout,
    settle=0.05,
    window_reader=read_window,
    progress=None,
):
    signal_level = int(scan["signal_level"])
    break_level = opposite_level(GPIO, signal_level)
    envelope = BurstEnvelope(emitter, scan["freq"], burst_on, burst_off)

    try:
        envelope.start()
        try:
            time.sleep(settle)
            active = window_reader(GPIO, sensor_pin, active_duration, interval, confirm_time)
        finally:
            envelope.stop()

        active_max_gap = (
            active["max_low_run_s"]
            if signal_level == GPIO.HIGH
            else active["max_high_run_s"]
        )
        timeout_result = calculate_signal_timeout(
            burst_on,
            burst_off,
            active_max_gap,
            max_signal_timeout,
        )

        if progress:
            progress({"phase": "break_test", "frequency_hz": int(scan["freq"])})
        broken = window_reader(
            GPIO,
            sensor_pin,
            break_duration,
            interval,
            timeout_result["signal_timeout"],
        )
        break_run_s = (
            broken["max_low_run_s"]
            if break_level == GPIO.LOW
            else broken["max_high_run_s"]
        )
        break_release_s = (
            broken["first_low_confirmed_at"]
            if break_level == GPIO.LOW
            else broken["first_high_confirmed_at"]
        )
        break_detected = (
            timeout_result["valid"]
            and break_release_s is not None
            and break_run_s >= timeout_result["signal_timeout"]
        )

        envelope.start()
        try:
            reacquire = window_reader(
                GPIO,
                sensor_pin,
                reacquire_duration,
                interval,
                confirm_time,
            )
        finally:
            envelope.stop()
    finally:
        envelope.stop()

    reacquire_s = (
        reacquire["first_high_confirmed_at"]
        if signal_level == GPIO.HIGH
        else reacquire["first_low_confirmed_at"]
    )
    residual_signal_samples = (
        broken["high"] if signal_level == GPIO.HIGH else broken["low"]
    )
    return {
        "active": active,
        "break": broken,
        "reacquire": reacquire,
        "signal_level": signal_level,
        "break_level": break_level,
        "active_max_gap_s": active_max_gap,
        "signal_timeout": timeout_result["signal_timeout"],
        "timeout_valid": timeout_result["valid"],
        "timeout_reason": timeout_result["reason"],
        "break_detected": break_detected,
        "break_release_s": break_release_s,
        "break_run_s": break_run_s,
        "residual_signal_samples": residual_signal_samples,
        "reacquire_s": reacquire_s,
        "reacquired": reacquire_s is not None,
    }


def find_sensitive_frequencies(GPIO, baseline, results, min_delta):
    sensitive = []
    for freq, stats in results:
        scored = score_frequency(GPIO, baseline, freq, stats)
        if scored["delta"] >= min_delta:
            sensitive.append(scored)
    return sensitive


def read_saturation_window(GPIO, sensor_pin, expected_level, duration, interval, saturation_gap):
    start = time.perf_counter()
    deadline = start + duration
    samples = 0
    expected_samples = 0
    high = 0
    low = 0
    transitions = 0
    first = None
    last = None
    first_expected_at = None
    last_expected_at = None
    missing_since = None
    lost_after = None

    while time.perf_counter() < deadline:
        now = time.perf_counter()
        elapsed = now - start
        level = GPIO.input(sensor_pin)

        if first is None:
            first = level
        if last is not None and level != last:
            transitions += 1
        last = level

        if level == GPIO.HIGH:
            high += 1
        else:
            low += 1

        if level == expected_level:
            expected_samples += 1
            if first_expected_at is None:
                first_expected_at = elapsed
            last_expected_at = elapsed
            missing_since = None
        else:
            if missing_since is None:
                missing_since = now
            if (
                lost_after is None
                and first_expected_at is not None
                and now - missing_since >= saturation_gap
            ):
                lost_after = max(0.0, missing_since - start)

        samples += 1
        time.sleep(interval)

    if samples and first_expected_at is None:
        lost_after = 0.0

    expected_pct = (expected_samples / samples * 100) if samples else 0
    high_pct = (high / samples * 100) if samples else 0
    low_pct = (low / samples * 100) if samples else 0
    return {
        "samples": samples,
        "expected_pct": round(expected_pct, 3),
        "expected_samples": expected_samples,
        "high": high,
        "low": low,
        "high_pct": round(high_pct, 3),
        "low_pct": round(low_pct, 3),
        "transitions": transitions,
        "first": first,
        "last": last,
        "first_expected_at": round(first_expected_at, 6) if first_expected_at is not None else None,
        "last_expected_at": round(last_expected_at, 6) if last_expected_at is not None else None,
        "lost_after": round(lost_after, 6) if lost_after is not None else None,
        "saturated": lost_after is not None,
    }


def frequency_preference_score(freq, preferred_frequency):
    if preferred_frequency is None:
        return 0
    return -abs(int(freq) - int(preferred_frequency))


def preference_bucket(value, tolerance):
    tolerance = max(float(tolerance or 0), 0.001)
    return round(float(value) / tolerance)


def choose_recommendation(
    sensitive,
    hold_results,
    preferred_frequency=None,
    preference_tolerance=1.0,
):
    if hold_results:
        stable = [item for item in hold_results if not item["hold"]["saturated"]]
        candidates = stable if stable else hold_results
        return max(
            candidates,
            key=lambda item: (
                not item["hold"]["saturated"],
                preference_bucket(item["hold"]["expected_pct"], preference_tolerance),
                preference_bucket(item["scan"]["delta"], preference_tolerance),
                frequency_preference_score(item["scan"]["freq"], preferred_frequency),
                item["hold"]["expected_pct"],
                item["scan"]["delta"],
                item["scan"]["signal_pct"],
            ),
        )["scan"]

    return max(
        sensitive,
        key=lambda item: (
            preference_bucket(item["delta"], preference_tolerance),
            preference_bucket(item["signal_pct"], preference_tolerance),
            frequency_preference_score(item["freq"], preferred_frequency),
            item["delta"],
            item["signal_pct"],
        ),
        default=None,
    )


def recommended_burst_times(hold_stats):
    default_on = 0.006
    default_off = 0.014
    if not hold_stats or hold_stats["lost_after"] is None:
        return default_on, default_off
    if hold_stats["lost_after"] <= 0:
        return 0.001, default_off

    burst_on = max(0.001, min(default_on, hold_stats["lost_after"] / 8))
    burst_off = max(default_off, burst_on * 9)
    return burst_on, burst_off


def build_recommendation(
    GPIO,
    duty,
    baseline,
    sensitive,
    hold_results,
    preferred_frequency=None,
    preference_tolerance=1.0,
):
    recommendation = choose_recommendation(
        sensitive,
        hold_results,
        preferred_frequency,
        preference_tolerance,
    )
    if recommendation is None:
        return None

    freq = recommendation["freq"]
    signal_level = recommendation["signal_level"]
    break_level = opposite_level(GPIO, signal_level)
    active_level = "LOW" if break_level == GPIO.LOW else "HIGH"
    matching_hold = next(
        (item["hold"] for item in hold_results if item["scan"]["freq"] == freq),
        None,
    )
    burst_on, burst_off = recommended_burst_times(matching_hold)
    signal_timeout = max(0.12, (burst_on + burst_off) * 6)
    baseline_signal_pct = level_pct(GPIO, baseline, signal_level)

    return {
        "frequency_hz": int(freq),
        "duty_cycle": float(duty),
        "burst_enabled": True,
        "burst_on": round(burst_on, 6),
        "burst_off": round(burst_off, 6),
        "sensor_active_level": active_level,
        "sensor_signal_timeout": round(signal_timeout, 6),
        "sensor_trigger_confirm": 0.002,
        "sensor_ready_min_ratio": 0.2,
        "aligned_level": int(signal_level),
        "aligned_level_name": level_name(GPIO, signal_level),
        "break_level": int(break_level),
        "break_level_name": level_name(GPIO, break_level),
        "baseline_signal_pct": round(baseline_signal_pct, 3),
        "scan_signal_pct": recommendation["signal_pct"],
        "scan_delta": recommendation["delta"],
        "saturation": matching_hold,
    }


def _ascending_metric(value):
    if value is None:
        return (1, math.inf)
    return (0, float(value))


def choose_operational_candidate(candidates, preferred_frequency=None):
    valid_candidates = [candidate for candidate in candidates if candidate.get("valid", True)]

    def rank(candidate):
        scan = candidate.get("scan") or {}
        margin = candidate.get("margin") or {}
        burst = candidate.get("burst") or {}
        break_test = candidate.get("break_test") or {}
        preferred_distance = (
            abs(int(scan.get("freq", 0)) - int(preferred_frequency))
            if preferred_frequency is not None
            else 0
        )
        return (
            _ascending_metric(margin.get("minimum_stable_duty")),
            _ascending_metric(burst.get("max_signal_gap")),
            _ascending_metric(break_test.get("break_release_s")),
            _ascending_metric(break_test.get("reacquire_s")),
            -float(scan.get("delta", -math.inf)),
            preferred_distance,
        )

    return min(valid_candidates, key=rank, default=None)


def calibration_result_is_valid(result):
    if not isinstance(result, dict) or result.get("ok") is not True:
        return False
    recommendation = result.get("recommendation")
    if not isinstance(recommendation, dict):
        return False
    try:
        frequency = int(recommendation.get("frequency_hz"))
        signal_timeout = float(recommendation.get("sensor_signal_timeout"))
    except (TypeError, ValueError):
        return False
    return frequency > 0 and 0 < signal_timeout <= 0.120


def _shortlist_key(candidate, preferred_frequency, preference_tolerance):
    scan = candidate["scan"]
    preferred_distance = (
        abs(int(scan["freq"]) - int(preferred_frequency))
        if preferred_frequency is not None
        else 0
    )
    return (
        -preference_bucket(scan.get("delta", 0.0), preference_tolerance),
        -preference_bucket(scan.get("signal_pct", 0.0), preference_tolerance),
        preferred_distance,
        -float(scan.get("delta", 0.0)),
        -float(scan.get("signal_pct", 0.0)),
    )


def _build_operational_recommendation(
    GPIO,
    duty,
    baseline,
    selected,
    burst_on,
    burst_off,
):
    if selected is None:
        return None

    scan = selected["scan"]
    signal_level = scan["signal_level"]
    break_level = opposite_level(GPIO, signal_level)
    break_test = selected["break_test"]
    margin = selected["margin"]
    recommendation = {
        "frequency_hz": int(scan["freq"]),
        "duty_cycle": float(duty),
        "burst_enabled": True,
        "burst_on": round(float(burst_on), 6),
        "burst_off": round(float(burst_off), 6),
        "sensor_active_level": "LOW" if break_level == GPIO.LOW else "HIGH",
        "sensor_signal_timeout": round(float(selected["signal_timeout"]), 6),
        "sensor_trigger_confirm": 0.002,
        "sensor_ready_min_ratio": 0.2,
        "aligned_level": int(signal_level),
        "aligned_level_name": level_name(GPIO, signal_level),
        "break_level": int(break_level),
        "break_level_name": level_name(GPIO, break_level),
        "baseline_signal_pct": round(level_pct(GPIO, baseline, signal_level), 3),
        "scan_signal_pct": scan["signal_pct"],
        "scan_delta": scan["delta"],
        "saturation": selected.get("hold"),
        "minimum_stable_duty": margin["minimum_stable_duty"],
        "burst_max_signal_gap": selected["burst"]["max_signal_gap"],
        "break_release_s": break_test["break_release_s"],
        "reacquire_s": break_test["reacquire_s"],
        "physical_break_validated": False,
    }
    return recommendation


def format_export_lines(recommendation):
    if not recommendation:
        return []
    lines = [
        f"export AGILITY_IR_FREQUENCY={recommendation['frequency_hz']}",
        f"export AGILITY_IR_DUTY_CYCLE={recommendation['duty_cycle']:g}",
    ]
    if recommendation.get("pwm_backend_env"):
        lines.append(f"export AGILITY_IR_PWM_BACKEND={recommendation['pwm_backend_env']}")
    lines.extend([
        "export AGILITY_IR_BURST_ENABLED=1",
        f"export AGILITY_IR_BURST_ON={recommendation['burst_on']:.4f}",
        f"export AGILITY_IR_BURST_OFF={recommendation['burst_off']:.4f}",
        f"export AGILITY_SENSOR_ACTIVE_LEVEL={recommendation['sensor_active_level']}",
        f"export AGILITY_SENSOR_SIGNAL_TIMEOUT={recommendation['sensor_signal_timeout']:.3f}",
        f"export AGILITY_SENSOR_TRIGGER_CONFIRM={recommendation['sensor_trigger_confirm']:.3f}",
        f"export AGILITY_SENSOR_READY_MIN_RATIO={recommendation['sensor_ready_min_ratio']:.1f}",
    ])
    return lines


def backend_env_name(active_backend):
    if active_backend == "kernel.sysfs.PWM":
        return "kernel_pwm"
    if active_backend == "pigpio.hardware_PWM":
        return "pigpio"
    if active_backend == "RPi.GPIO.PWM":
        return "rpi_gpio"
    return None


def run_ir_calibration(
    GPIO,
    sensor_pin=17,
    emitter_pin=18,
    duty=50.0,
    duration=0.35,
    interval=0.001,
    settle=0.08,
    recovery=1.0,
    start=10000,
    stop=60000,
    step=1000,
    sensitivity_delta=25.0,
    hold_duration=1.0,
    saturation_gap=0.05,
    freqs=None,
    skip_hold=False,
    preferred_frequency=50000,
    preference_tolerance=1.0,
    pwm_backend="auto",
    pwm_chip=0,
    pwm_channel=None,
    progress=None,
    noise_confirm_time=0.002,
    finalist_count=5,
    burst_on=0.006,
    burst_off=0.014,
    burst_test_duration=1.0,
    break_duration=0.25,
    reacquire_duration=0.5,
    max_signal_timeout=0.12,
):
    if GPIO is None:
        raise CalibrationError("RPi.GPIO indisponivel")

    started_perf = time.perf_counter()
    started_at = datetime.now().isoformat()
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    emitter = Emitter(
        GPIO,
        emitter_pin,
        duty,
        pwm_backend=pwm_backend,
        pwm_chip=pwm_chip,
        pwm_channel=pwm_channel,
    )
    emitter.setup()

    effective_sensitivity_delta = max(float(sensitivity_delta), 25.0)
    effective_noise_confirm_time = max(float(noise_confirm_time), 0.002)
    effective_finalist_count = min(5, max(1, int(finalist_count)))
    effective_burst_on = max(0.0005, float(burst_on))
    effective_burst_off = max(0.0005, float(burst_off))
    options = {
        "sensor_pin": int(sensor_pin),
        "emitter_pin": int(emitter_pin),
        "duty": float(duty),
        "duration": float(duration),
        "interval": float(interval),
        "settle": float(settle),
        "recovery": float(recovery),
        "start": int(start),
        "stop": int(stop),
        "step": int(step),
        "sensitivity_delta": effective_sensitivity_delta,
        "noise_confirm_time": effective_noise_confirm_time,
        "finalist_count": effective_finalist_count,
        "hold_duration": float(hold_duration),
        "saturation_gap": float(saturation_gap),
        "skip_hold": bool(skip_hold),
        "preferred_frequency": int(preferred_frequency) if preferred_frequency is not None else None,
        "preference_tolerance": float(preference_tolerance),
        "pwm_backend_requested": normalize_pwm_backend(pwm_backend),
        "pwm_chip": int(pwm_chip),
        "pwm_channel": kernel_pwm_channel_for_pin(emitter_pin, pwm_channel),
        "burst_on": effective_burst_on,
        "burst_off": effective_burst_off,
        "burst_test_duration": float(burst_test_duration),
        "break_duration": float(break_duration),
        "reacquire_duration": float(reacquire_duration),
        "max_signal_timeout": min(float(max_signal_timeout), 0.120),
        "frequencies": frequency_list(start, stop, step, freqs),
    }

    try:
        emitter.off()
        time.sleep(recovery)
        noise_scan = []
        for window_index, freq in enumerate(options["frequencies"]):
            if progress:
                progress({"phase": "noise_scan", "frequency_hz": int(freq)})
            stats = read_window(
                GPIO,
                sensor_pin,
                duration,
                interval,
                effective_noise_confirm_time,
            )
            noise_scan.append({
                "window_index": int(window_index),
                "candidate_frequency_hz": int(freq),
                "stats": stats,
            })

        if noise_scan:
            first_noise_stats = noise_scan[0]["stats"]
        else:
            if progress:
                progress({"phase": "noise_scan", "message": "lendo emissor desligado"})
            first_noise_stats = read_window(
                GPIO,
                sensor_pin,
                duration,
                interval,
                effective_noise_confirm_time,
            )

        active_results = []
        for freq in options["frequencies"]:
            if progress:
                progress({"phase": "active_scan", "frequency_hz": int(freq)})
            emitter.off()
            time.sleep(recovery)
            stats = read_burst_window(
                GPIO,
                sensor_pin,
                emitter,
                freq,
                effective_burst_on,
                effective_burst_off,
                duration,
                interval,
                effective_noise_confirm_time,
                settle=settle,
                window_reader=read_window,
            )
            active_results.append((int(freq), stats))

        sensitive, rejected = classify_frequency_results(
            GPIO,
            noise_scan,
            active_results,
            effective_sensitivity_delta,
            effective_noise_confirm_time,
        )
        hold_results = []
        shortlist_pool = []
        for scan in sensitive:
            freq = scan["freq"]
            if progress:
                progress({"phase": "hold", "frequency_hz": int(freq)})
            hold = None
            if not skip_hold:
                emitter.off()
                time.sleep(recovery)
                emitter.set_frequency(freq)
                time.sleep(settle)
                hold = read_saturation_window(
                    GPIO,
                    sensor_pin,
                    scan["signal_level"],
                    hold_duration,
                    interval,
                    saturation_gap,
                )
                emitter.off()
            hold_item = {"scan": scan, "hold": hold}
            hold_results.append(hold_item)
            shortlist_pool.append(hold_item)

        shortlist = sorted(
            shortlist_pool,
            key=lambda item: _shortlist_key(
                item,
                preferred_frequency,
                preference_tolerance,
            ),
        )[:effective_finalist_count]

        margin_results = []
        burst_results = []
        break_tests = []
        finalist_results = []
        for finalist in shortlist:
            scan = finalist["scan"]
            freq = scan["freq"]
            if progress:
                progress({"phase": "margin_test", "frequency_hz": int(freq)})
            margin_result = run_margin_test(
                GPIO,
                sensor_pin,
                emitter,
                scan,
                scan["noise_stats"],
                duration,
                interval,
                settle,
                recovery,
                effective_sensitivity_delta,
                effective_noise_confirm_time,
                effective_burst_on,
                effective_burst_off,
            )
            margin_entry = {"freq": int(freq), **margin_result}
            margin_results.append(margin_entry)
            reasons = []
            burst_entry = None
            break_entry = None
            signal_timeout = None

            if margin_result["minimum_stable_duty"] is None:
                reasons.append("insufficient_contrast")
            else:
                if progress:
                    progress({"phase": "burst_test", "frequency_hz": int(freq)})
                operational = run_operational_test(
                    GPIO,
                    sensor_pin,
                    emitter,
                    scan,
                    effective_burst_on,
                    effective_burst_off,
                    burst_test_duration,
                    break_duration,
                    reacquire_duration,
                    interval,
                    effective_noise_confirm_time,
                    max_signal_timeout,
                    settle=settle,
                    progress=progress,
                )
                signal_timeout = operational["signal_timeout"]
                burst_entry = {
                    "freq": int(freq),
                    "max_signal_gap": operational["active_max_gap_s"],
                    "stats": operational["active"],
                }
                break_entry = {
                    "freq": int(freq),
                    "break_detected": operational["break_detected"],
                    "break_release_s": operational["break_release_s"],
                    "break_run_s": operational["break_run_s"],
                    "residual_signal_samples": operational["residual_signal_samples"],
                    "reacquire_s": operational["reacquire_s"],
                    "reacquired": operational["reacquired"],
                    "timeout": signal_timeout,
                    "signal_timeout": signal_timeout,
                    "timeout_valid": operational["timeout_valid"],
                }
                burst_results.append(burst_entry)
                break_tests.append(break_entry)
                if not operational["timeout_valid"]:
                    reasons.append("signal_gap_too_large")
                if not operational["break_detected"] or not operational["reacquired"]:
                    reasons.append("break_not_detected")

            candidate = {
                "scan": scan,
                "hold": finalist["hold"],
                "margin": margin_entry,
                "burst": burst_entry,
                "break_test": break_entry,
                "signal_timeout": signal_timeout,
                "valid": not reasons,
                "reasons": reasons,
            }
            finalist_results.append(candidate)
            if reasons:
                rejected.append({
                    **scan,
                    "hold": finalist["hold"],
                    "margin": margin_entry,
                    "burst": burst_entry,
                    "break_test": break_entry,
                    "signal_timeout": signal_timeout,
                    "valid": False,
                    "reasons": reasons,
                })

        if progress:
            progress({"phase": "select", "frequency_hz": None})
        selected = choose_operational_candidate(finalist_results, preferred_frequency)
        baseline = selected["scan"]["noise_stats"] if selected is not None else first_noise_stats
        recommendation = _build_operational_recommendation(
            GPIO,
            duty,
            baseline,
            selected,
            effective_burst_on,
            effective_burst_off,
        )
        if recommendation is not None:
            recommendation["pwm_backend"] = emitter.active_backend
            recommendation["pwm_backend_env"] = backend_env_name(emitter.active_backend)
            recommendation["exports"] = format_export_lines(recommendation)

        reason_counts = {}
        for item in rejected:
            for reason in item.get("reasons", []):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        diagnostics = {
            "noise_windows": len(noise_scan),
            "active_scans": len(active_results),
            "sensitive_candidates": len(sensitive),
            "finalists": len(finalist_results),
            "valid_candidates": sum(item["valid"] for item in finalist_results),
            "rejected_candidates": len(rejected),
            "reason_counts": reason_counts,
            "continuous_suppressed_candidates": sum(
                bool(item.get("hold") and item["hold"].get("saturated"))
                for item in hold_results
            ),
            "continuous_suppression_frequencies": [
                int(item["scan"]["freq"])
                for item in hold_results
                if item.get("hold") and item["hold"].get("saturated")
            ],
            "finalist_results": finalist_results,
            "physical_break_validated": False,
            "physical_validation_warning": (
                "A interrupcao foi simulada; valide uma quebra fisica do feixe antes da prova."
            ),
        }

        scan_results = [
            {"freq": int(freq), "stats": stats}
            for freq, stats in active_results
        ]
        finished_at = datetime.now().isoformat()
        duration_s = time.perf_counter() - started_perf
        return {
            "ok": recommendation is not None,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": round(duration_s, 3),
            "emitter_backend": emitter.active_backend,
            "options": options,
            "baseline": baseline,
            "scan": scan_results,
            "sensitive": sensitive,
            "hold": hold_results,
            "recommendation": recommendation,
            "noise_scan": noise_scan,
            "rejected": rejected,
            "margin": margin_results,
            "burst": burst_results,
            "break_tests": break_tests,
            "diagnostics": diagnostics,
        }
    finally:
        emitter.cleanup()


def result_to_json(result):
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Testa receptor IR lendo GPIO bruto e varrendo frequencias do emissor."
    )
    parser.add_argument("--sensor-pin", type=int, default=17, help="GPIO BCM do OUT do receptor.")
    parser.add_argument("--emitter-pin", type=int, default=18, help="GPIO BCM do LED IR.")
    parser.add_argument("--duty", type=float, default=50.0, help="Duty cycle do emissor IR.")
    parser.add_argument("--duration", type=float, default=0.35, help="Tempo de leitura por frequencia.")
    parser.add_argument("--interval", type=float, default=0.001, help="Intervalo entre leituras do GPIO.")
    parser.add_argument("--settle", type=float, default=0.08, help="Espera apos mudar frequencia.")
    parser.add_argument(
        "--recovery",
        type=float,
        default=1.0,
        help="Tempo com emissor desligado entre emissoes para o receptor sair de saturacao.",
    )
    parser.add_argument("--start", type=int, default=10000, help="Frequencia inicial da varredura.")
    parser.add_argument("--stop", type=int, default=60000, help="Frequencia final da varredura.")
    parser.add_argument("--step", type=int, default=1000, help="Passo da varredura.")
    parser.add_argument(
        "--sensitivity-delta",
        type=float,
        default=25.0,
        help="Mudanca minima em pontos percentuais para considerar uma frequencia sensivel.",
    )
    parser.add_argument(
        "--noise-confirm-time",
        type=float,
        default=0.002,
        help="Tempo minimo de sinal continuo para marcar contaminacao com emissor desligado.",
    )
    parser.add_argument(
        "--finalist-count",
        type=int,
        default=5,
        help="Numero maximo de candidatos limpos submetidos aos testes operacionais.",
    )
    parser.add_argument(
        "--hold-duration",
        type=float,
        default=1.0,
        help="Tempo de emissao continua para testar saturacao nas frequencias sensiveis.",
    )
    parser.add_argument(
        "--saturation-gap",
        type=float,
        default=0.05,
        help="Tempo sem o nivel de sinal esperado para marcar saturacao/perda de sinal.",
    )
    parser.add_argument(
        "--skip-hold",
        action="store_true",
        help="Nao executa o teste de saturacao de 1s nas frequencias sensiveis.",
    )
    parser.add_argument(
        "--burst-on",
        type=float,
        default=0.006,
        help="Tempo ligado de cada rajada IR em segundos.",
    )
    parser.add_argument(
        "--burst-off",
        type=float,
        default=0.014,
        help="Tempo desligado entre rajadas IR em segundos.",
    )
    parser.add_argument(
        "--burst-test-duration",
        type=float,
        default=1.0,
        help="Duracao do teste ativo em rajadas.",
    )
    parser.add_argument(
        "--break-duration",
        type=float,
        default=0.250,
        help="Duracao da interrupcao simulada do feixe.",
    )
    parser.add_argument(
        "--reacquire-duration",
        type=float,
        default=0.500,
        help="Janela para medir a recuperacao do sinal depois da interrupcao.",
    )
    parser.add_argument(
        "--max-signal-timeout",
        type=float,
        default=0.120,
        help="Maior timeout de sinal permitido; o teto de seguranca e 0.120s.",
    )
    parser.add_argument(
        "--freqs",
        help="Lista de frequencias separadas por virgula. Ex: 36000,38000,40000,50000",
    )
    parser.add_argument(
        "--preferred-frequency",
        type=int,
        default=50000,
        help="Frequencia preferida para desempate quando varias frequencias responderem igualmente.",
    )
    parser.add_argument(
        "--preference-tolerance",
        type=float,
        default=1.0,
        help="Tolerancia em pontos percentuais para considerar frequencias equivalentes.",
    )
    parser.add_argument(
        "--pwm-backend",
        default="auto",
        choices=("auto", "kernel_pwm", "sysfs", "pigpio", "rpi_gpio"),
        help="Backend de PWM para o emissor.",
    )
    parser.add_argument(
        "--pwm-chip",
        type=int,
        default=0,
        help="Numero do pwmchip do kernel. Para Raspberry Pi Zero 2 W normalmente e 0.",
    )
    parser.add_argument(
        "--pwm-channel",
        type=int,
        help="Canal PWM do kernel. Com dtoverlay=pwm-2chan, GPIO18 normalmente usa canal 0.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime o resultado bruto em JSON.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Apenas mostra o nivel bruto do sensor em tempo real, sem ligar o emissor.",
    )
    return parser
