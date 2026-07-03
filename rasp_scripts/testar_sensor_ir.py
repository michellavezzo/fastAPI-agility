#!/usr/bin/env python3
import argparse
import sys
import time


SYSTEM_DIST_PACKAGES = (
    "/usr/lib/python3/dist-packages",
    "/usr/local/lib/python3/dist-packages",
)


def parse_args():
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
        "--watch",
        action="store_true",
        help="Apenas mostra o nivel bruto do sensor em tempo real, sem ligar o emissor.",
    )
    return parser.parse_args()


def load_gpio():
    try:
        import RPi.GPIO as GPIO
        return GPIO
    except Exception as exc:
        first_error = exc

    for path in SYSTEM_DIST_PACKAGES:
        if path not in sys.path:
            sys.path.append(path)
        try:
            import RPi.GPIO as GPIO
            print(f"RPi.GPIO carregado via pacote do sistema: {path}")
            return GPIO
        except Exception:
            continue

    print(f"ERRO: nao foi possivel importar RPi.GPIO: {type(first_error).__name__}: {first_error}")
    print("O pacote APT pode estar instalado, mas fora do caminho do venv.")
    print("Tente: PYTHONPATH=/usr/lib/python3/dist-packages python rasp_scripts/testar_sensor_ir.py")
    print("Ou rode com o Python do sistema: /usr/bin/python3 rasp_scripts/testar_sensor_ir.py")
    raise SystemExit(1)


def load_pigpio():
    try:
        import pigpio
    except Exception:
        for path in SYSTEM_DIST_PACKAGES:
            if path not in sys.path:
                sys.path.append(path)
            try:
                import pigpio
                break
            except Exception:
                pigpio = None
        if pigpio is None:
            return None
    pi = pigpio.pi()
    if not pi.connected:
        return None
    return pigpio, pi


def frequency_list(args):
    if args.freqs:
        return [int(part.strip()) for part in args.freqs.split(",") if part.strip()]
    return list(range(args.start, args.stop + 1, args.step))


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
        "high_pct": high_pct,
        "low_pct": low_pct,
        "transitions": transitions,
        "first": first,
        "last": last,
    }


class Emitter:
    def __init__(self, GPIO, pin, duty):
        self.GPIO = GPIO
        self.pin = pin
        self.duty = max(0.0, min(100.0, duty))
        self.pwm = None
        self.pigpio = None
        self.pi = None

    def setup(self):
        pig = load_pigpio()
        if pig is not None:
            self.pigpio, self.pi = pig
            try:
                self.pi.set_mode(self.pin, self.pigpio.OUTPUT)
                print("Emissor: usando pigpio.hardware_PWM.")
                return
            except Exception as exc:
                print(f"Aviso: pigpio falhou, usando RPi.GPIO.PWM: {exc}")
                self.pi.stop()
                self.pigpio = None
                self.pi = None

        self.GPIO.setup(self.pin, self.GPIO.OUT, initial=self.GPIO.LOW)
        self.pwm = self.GPIO.PWM(self.pin, 38000)
        self.pwm.start(0)
        print("Emissor: usando RPi.GPIO.PWM.")

    def set_frequency(self, frequency):
        if self.pi is not None:
            self.pi.hardware_PWM(self.pin, int(frequency), int(self.duty * 10000))
            return

        self.pwm.ChangeFrequency(int(frequency))
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
            if self.pi is not None:
                self.pi.stop()


def dominant(stats):
    if stats["low_pct"] >= 80:
        return "LOW"
    if stats["high_pct"] >= 80:
        return "HIGH"
    return "OSCILANDO"


def print_stats(label, stats):
    print(
        f"{label:>10}  LOW={stats['low_pct']:6.1f}%  HIGH={stats['high_pct']:6.1f}%  "
        f"trans={stats['transitions']:3d}  last={stats['last']}"
    )


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
        "freq": freq,
        "stats": stats,
        "signal_level": signal_level,
        "signal_pct": level_pct(GPIO, stats, signal_level),
        "delta": delta,
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
        "expected_pct": expected_pct,
        "expected_samples": expected_samples,
        "high": high,
        "low": low,
        "high_pct": high_pct,
        "low_pct": low_pct,
        "transitions": transitions,
        "first": first,
        "last": last,
        "first_expected_at": first_expected_at,
        "last_expected_at": last_expected_at,
        "lost_after": lost_after,
        "saturated": lost_after is not None,
    }


def print_hold_stats(GPIO, label, expected_level, stats, duration):
    if stats["lost_after"] is None:
        saturation = f"sem perda em {duration:g}s"
    else:
        saturation = f"perda apos {stats['lost_after']:.3f}s"

    print(
        f"{label:>10}  sinal={level_name(GPIO, expected_level):>4} "
        f"{stats['expected_pct']:6.1f}%  LOW={stats['low_pct']:6.1f}% "
        f"HIGH={stats['high_pct']:6.1f}%  trans={stats['transitions']:3d}  {saturation}"
    )


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


def print_recommendation(GPIO, args, baseline, sensitive, hold_results):
    recommendation = choose_recommendation(sensitive, hold_results)

    print("\nDiagnostico:")
    if recommendation is None:
        print(
            "- Nenhuma frequencia testada mudou claramente o GPIO. Verifique pinagem do sensor, "
            "emissor IR, comprimento de onda do LED e se o receptor e demodulado."
        )
        return

    freq = recommendation["freq"]
    signal_level = recommendation["signal_level"]
    break_level = opposite_level(GPIO, signal_level)
    active_level = "LOW" if break_level == GPIO.LOW else "HIGH"
    baseline_signal_pct = level_pct(GPIO, baseline, signal_level)
    matching_hold = next(
        (item["hold"] for item in hold_results if item["scan"]["freq"] == freq),
        None,
    )
    burst_on, burst_off = recommended_burst_times(matching_hold)
    signal_timeout = max(0.03, (burst_on + burst_off) * 6)

    print(
        f"- Melhor frequencia candidata: {freq}Hz. Nivel de sinal/alinhado: "
        f"{level_name(GPIO, signal_level)} subiu de {baseline_signal_pct:.1f}% "
        f"para {recommendation['signal_pct']:.1f}%."
    )
    print(
        f"- Interpretação: feixe alinhado = {level_name(GPIO, signal_level)}; "
        f"feixe quebrado/sem sinal = {level_name(GPIO, break_level)}."
    )
    if matching_hold is not None:
        if matching_hold["lost_after"] is None:
            print(
                "- Saturacao: nao houve perda do nivel de sinal durante "
                f"o teste de {args.hold_duration:g}s."
            )
        else:
            print(
                f"- Saturacao: nivel de sinal perdido apos {matching_hold['lost_after']:.3f}s "
                "com portadora continua."
            )
    print("- Configuracao recomendada para o backend:")
    print(f"  export AGILITY_IR_FREQUENCY={freq}")
    print(f"  export AGILITY_IR_DUTY_CYCLE={args.duty:g}")
    print("  export AGILITY_IR_BURST_ENABLED=1")
    print(f"  export AGILITY_IR_BURST_ON={burst_on:.4f}")
    print(f"  export AGILITY_IR_BURST_OFF={burst_off:.4f}")
    print(f"  export AGILITY_SENSOR_ACTIVE_LEVEL={active_level}")
    print(f"  export AGILITY_SENSOR_SIGNAL_TIMEOUT={signal_timeout:.3f}")
    print("  export AGILITY_SENSOR_TRIGGER_CONFIRM=0.002")
    print("  export AGILITY_SENSOR_READY_MIN_RATIO=0.2")


def watch(GPIO, sensor_pin, interval):
    print("Mostrando nivel bruto do sensor. Ctrl+C para sair.")
    last = None
    try:
        while True:
            level = GPIO.input(sensor_pin)
            if level != last:
                print(f"{time.strftime('%H:%M:%S')} nivel={level}")
                last = level
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def main():
    args = parse_args()
    GPIO = load_gpio()
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(args.sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    if args.watch:
        try:
            watch(GPIO, args.sensor_pin, args.interval)
        finally:
            GPIO.cleanup()
        return

    emitter = Emitter(GPIO, args.emitter_pin, args.duty)
    emitter.setup()

    try:
        print("Pare o backend antes deste teste para evitar disputa pelo GPIO.")
        print(f"Sensor GPIO{args.sensor_pin}; emissor GPIO{args.emitter_pin}; duty {args.duty}%.")
        emitter.off()
        time.sleep(args.recovery)
        baseline = read_window(GPIO, args.sensor_pin, args.duration, args.interval)
        print_stats("OFF", baseline)

        results = []
        for freq in frequency_list(args):
            emitter.off()
            time.sleep(args.recovery)
            emitter.set_frequency(freq)
            time.sleep(args.settle)
            stats = read_window(GPIO, args.sensor_pin, args.duration, args.interval)
            results.append((freq, stats))
            print_stats(f"{freq}Hz", stats)
            emitter.off()

        emitter.off()

        sensitive = find_sensitive_frequencies(
            GPIO,
            baseline,
            results,
            args.sensitivity_delta,
        )
        hold_results = []
        if sensitive and not args.skip_hold:
            print("\nTeste de saturacao com portadora continua:")
            for scan in sensitive:
                freq = scan["freq"]
                emitter.off()
                time.sleep(args.recovery)
                emitter.set_frequency(freq)
                time.sleep(args.settle)
                hold = read_saturation_window(
                    GPIO,
                    args.sensor_pin,
                    scan["signal_level"],
                    args.hold_duration,
                    args.interval,
                    args.saturation_gap,
                )
                hold_results.append({"scan": scan, "hold": hold})
                print_hold_stats(
                    GPIO,
                    f"{freq}Hz",
                    scan["signal_level"],
                    hold,
                    args.hold_duration,
                )
            emitter.off()

        print_recommendation(GPIO, args, baseline, sensitive, hold_results)
    finally:
        emitter.cleanup()
        GPIO.cleanup()


if __name__ == "__main__":
    sys.exit(main())
