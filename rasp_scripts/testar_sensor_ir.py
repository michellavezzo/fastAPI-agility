#!/usr/bin/env python3
import argparse
import sys
import time


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
    parser.add_argument("--start", type=int, default=30000, help="Frequencia inicial da varredura.")
    parser.add_argument("--stop", type=int, default=60000, help="Frequencia final da varredura.")
    parser.add_argument("--step", type=int, default=1000, help="Passo da varredura.")
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
    except Exception as exc:
        print(f"ERRO: nao foi possivel importar RPi.GPIO: {type(exc).__name__}: {exc}")
        print("Instale no Raspberry Pi com: sudo apt install python3-rpi.gpio")
        raise SystemExit(1)
    return GPIO


def load_pigpio():
    try:
        import pigpio
    except Exception:
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
        time.sleep(args.settle)
        baseline = read_window(GPIO, args.sensor_pin, args.duration, args.interval)
        print_stats("OFF", baseline)

        results = []
        for freq in frequency_list(args):
            emitter.set_frequency(freq)
            time.sleep(args.settle)
            stats = read_window(GPIO, args.sensor_pin, args.duration, args.interval)
            results.append((freq, stats))
            print_stats(f"{freq}Hz", stats)

        emitter.off()

        baseline_dom = dominant(baseline)
        best_low = max(results, key=lambda item: item[1]["low_pct"], default=None)
        best_high = max(results, key=lambda item: item[1]["high_pct"], default=None)

        print("\nDiagnostico:")
        if baseline_dom == "LOW":
            print(
                "- Com o emissor desligado, o GPIO ja fica LOW. Isso aponta mais para "
                "pinagem, alimentacao, modulo incompatível ou saida presa do que para frequencia."
            )
        elif best_low and best_low[1]["low_pct"] - baseline["low_pct"] >= 40:
            print(
                f"- Receptor parece responder perto de {best_low[0]}Hz "
                f"(LOW subiu para {best_low[1]['low_pct']:.1f}%)."
            )
        elif best_high and best_high[1]["high_pct"] - baseline["high_pct"] >= 40:
            print(
                f"- Receptor parece responder com logica invertida perto de {best_high[0]}Hz "
                f"(HIGH subiu para {best_high[1]['high_pct']:.1f}%)."
            )
        else:
            print(
                "- Nenhuma frequencia testada mudou claramente o GPIO. Verifique pinagem do sensor, "
                "emissor IR, comprimento de onda do LED e se o receptor e demodulado."
            )
    finally:
        emitter.cleanup()
        GPIO.cleanup()


if __name__ == "__main__":
    sys.exit(main())
