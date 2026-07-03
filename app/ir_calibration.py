import argparse
import json
import sys
import time
from datetime import datetime


SYSTEM_DIST_PACKAGES = (
    "/usr/lib/python3/dist-packages",
    "/usr/local/lib/python3/dist-packages",
)

HARDWARE_PWM_PINS = {12, 13, 18, 19}


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
    if value not in ("auto", "pigpio", "rpi_gpio"):
        return "auto"
    return value


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


def read_window(GPIO, sensor_pin, duration, interval):
    end = time.perf_counter() + duration
    samples = 0
    high = 0
    low = 0
    transitions = 0
    first = None
    last = None

    while time.perf_counter() < end:
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
        samples += 1
        time.sleep(interval)

    high_pct = (high / samples * 100) if samples else 0
    low_pct = (low / samples * 100) if samples else 0
    return {
        "samples": samples,
        "high": high,
        "low": low,
        "high_pct": round(high_pct, 3),
        "low_pct": round(low_pct, 3),
        "transitions": transitions,
        "first": first,
        "last": last,
    }


class Emitter:
    def __init__(self, GPIO, pin, duty, pwm_backend="auto"):
        self.GPIO = GPIO
        self.pin = int(pin)
        self.duty = max(0.0, min(100.0, float(duty)))
        self.pwm_backend = normalize_pwm_backend(pwm_backend)
        self.active_backend = None
        self.pwm = None
        self.pigpio = None
        self.pi = None

    def setup(self):
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
        if self.pi is not None:
            self.pi.hardware_PWM(self.pin, frequency, int(self.duty * 10000))
            return

        self.pwm.ChangeFrequency(frequency)
        self.pwm.ChangeDutyCycle(self.duty)

    def off(self):
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
            if self.pi is not None:
                self.pi.stop()
                self.pi = None


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


def choose_recommendation(sensitive, hold_results):
    if hold_results:
        stable = [item for item in hold_results if not item["hold"]["saturated"]]
        candidates = stable if stable else hold_results
        return max(
            candidates,
            key=lambda item: (
                not item["hold"]["saturated"],
                item["hold"]["expected_pct"],
                item["scan"]["delta"],
            ),
        )["scan"]

    return max(sensitive, key=lambda item: (item["delta"], item["signal_pct"]), default=None)


def recommended_burst_times(hold_stats):
    default_on = 0.002
    default_off = 0.002
    if not hold_stats or hold_stats["lost_after"] is None:
        return default_on, default_off
    if hold_stats["lost_after"] <= 0:
        return 0.0005, default_off

    burst_on = max(0.0005, min(default_on, hold_stats["lost_after"] / 4))
    burst_off = max(default_off, burst_on)
    return burst_on, burst_off


def build_recommendation(GPIO, duty, baseline, sensitive, hold_results):
    recommendation = choose_recommendation(sensitive, hold_results)
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
    signal_timeout = max(0.03, (burst_on + burst_off) * 6)
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


def format_export_lines(recommendation):
    if not recommendation:
        return []
    return [
        f"export AGILITY_IR_FREQUENCY={recommendation['frequency_hz']}",
        f"export AGILITY_IR_DUTY_CYCLE={recommendation['duty_cycle']:g}",
        "export AGILITY_IR_BURST_ENABLED=1",
        f"export AGILITY_IR_BURST_ON={recommendation['burst_on']:.4f}",
        f"export AGILITY_IR_BURST_OFF={recommendation['burst_off']:.4f}",
        f"export AGILITY_SENSOR_ACTIVE_LEVEL={recommendation['sensor_active_level']}",
        f"export AGILITY_SENSOR_SIGNAL_TIMEOUT={recommendation['sensor_signal_timeout']:.3f}",
        f"export AGILITY_SENSOR_TRIGGER_CONFIRM={recommendation['sensor_trigger_confirm']:.3f}",
        f"export AGILITY_SENSOR_READY_MIN_RATIO={recommendation['sensor_ready_min_ratio']:.1f}",
    ]


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
    pwm_backend="auto",
    progress=None,
):
    if GPIO is None:
        raise CalibrationError("RPi.GPIO indisponivel")

    started_perf = time.perf_counter()
    started_at = datetime.now().isoformat()
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    emitter = Emitter(GPIO, emitter_pin, duty, pwm_backend=pwm_backend)
    emitter.setup()

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
        "sensitivity_delta": float(sensitivity_delta),
        "hold_duration": float(hold_duration),
        "saturation_gap": float(saturation_gap),
        "skip_hold": bool(skip_hold),
        "pwm_backend_requested": normalize_pwm_backend(pwm_backend),
        "frequencies": frequency_list(start, stop, step, freqs),
    }

    try:
        if progress:
            progress({"phase": "baseline", "message": "lendo sensor com emissor desligado"})
        emitter.off()
        time.sleep(recovery)
        baseline = read_window(GPIO, sensor_pin, duration, interval)

        results = []
        for freq in options["frequencies"]:
            if progress:
                progress({"phase": "scan", "frequency_hz": int(freq)})
            emitter.off()
            time.sleep(recovery)
            emitter.set_frequency(freq)
            time.sleep(settle)
            stats = read_window(GPIO, sensor_pin, duration, interval)
            results.append((freq, stats))
            emitter.off()

        sensitive = find_sensitive_frequencies(GPIO, baseline, results, sensitivity_delta)
        hold_results = []
        if sensitive and not skip_hold:
            for scan in sensitive:
                freq = scan["freq"]
                if progress:
                    progress({"phase": "hold", "frequency_hz": int(freq)})
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
                hold_results.append({"scan": scan, "hold": hold})
            emitter.off()

        recommendation = build_recommendation(GPIO, duty, baseline, sensitive, hold_results)
        if recommendation is not None:
            recommendation["pwm_backend"] = emitter.active_backend
            recommendation["exports"] = format_export_lines(recommendation)

        scan_results = [
            {
                "freq": int(freq),
                "stats": stats,
            }
            for freq, stats in results
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
        "--freqs",
        help="Lista de frequencias separadas por virgula. Ex: 36000,38000,40000,56000",
    )
    parser.add_argument(
        "--pwm-backend",
        default="auto",
        choices=("auto", "pigpio", "rpi_gpio"),
        help="Backend de PWM para o emissor.",
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
