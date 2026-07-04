#!/usr/bin/env python3
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.ir_calibration import (  # noqa: E402
    CalibrationError,
    build_arg_parser,
    format_export_lines,
    level_name,
    load_gpio,
    result_to_json,
    run_ir_calibration,
)


def print_stats(label, stats):
    print(
        f"{label:>10}  LOW={stats['low_pct']:6.1f}%  HIGH={stats['high_pct']:6.1f}%  "
        f"trans={stats['transitions']:3d}  last={stats['last']}"
    )


def print_hold_stats(label, expected_level_name, stats, duration):
    if stats["lost_after"] is None:
        saturation = f"sem perda em {duration:g}s"
    else:
        saturation = f"perda apos {stats['lost_after']:.3f}s"

    print(
        f"{label:>10}  sinal={expected_level_name:>4} "
        f"{stats['expected_pct']:6.1f}%  LOW={stats['low_pct']:6.1f}% "
        f"HIGH={stats['high_pct']:6.1f}%  trans={stats['transitions']:3d}  {saturation}"
    )


def print_recommendation(result):
    recommendation = result["recommendation"]
    print("\nDiagnostico:")
    if recommendation is None:
        print(
            "- Nenhuma frequencia testada mudou claramente o GPIO. Verifique pinagem do sensor, "
            "emissor IR, comprimento de onda do LED e se o receptor e demodulado."
        )
        return

    print(
        f"- Melhor frequencia candidata: {recommendation['frequency_hz']}Hz. Nivel de sinal/alinhado: "
        f"{recommendation['aligned_level_name']} subiu de "
        f"{recommendation['baseline_signal_pct']:.1f}% para "
        f"{recommendation['scan_signal_pct']:.1f}%."
    )
    print(
        f"- Interpretação: feixe alinhado = {recommendation['aligned_level_name']}; "
        f"feixe quebrado/sem sinal = {recommendation['break_level_name']}."
    )
    saturation = recommendation.get("saturation")
    if saturation is not None:
        if saturation["lost_after"] is None:
            print("- Saturacao: nao houve perda do nivel de sinal durante o teste.")
        else:
            print(
                f"- Saturacao: nivel de sinal perdido apos {saturation['lost_after']:.3f}s "
                "com portadora continua."
            )
    print(f"- Backend PWM usado no teste: {recommendation['pwm_backend']}.")
    print("- Configuracao recomendada para o backend:")
    for line in format_export_lines(recommendation):
        print(f"  {line}")


def watch(GPIO, sensor_pin, interval):
    print("Mostrando nivel bruto do sensor. Ctrl+C para sair.")
    last = None
    try:
        while True:
            level = GPIO.input(sensor_pin)
            if level != last:
                print(f"{time.strftime('%H:%M:%S')} nivel={level} ({level_name(GPIO, level)})")
                last = level
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        GPIO = load_gpio()
    except CalibrationError as exc:
        print(f"ERRO: {exc}")
        print("Tente: PYTHONPATH=/usr/lib/python3/dist-packages python rasp_scripts/testar_sensor_ir.py")
        print("Ou rode com o Python do sistema: /usr/bin/python3 rasp_scripts/testar_sensor_ir.py")
        return 1

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(args.sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    if args.watch:
        try:
            watch(GPIO, args.sensor_pin, args.interval)
        finally:
            GPIO.cleanup()
        return 0

    print("Pare o backend antes deste teste para evitar disputa pelo GPIO.")
    print(
        f"Sensor GPIO{args.sensor_pin}; emissor GPIO{args.emitter_pin}; "
        f"duty {args.duty}%; PWM {args.pwm_backend}."
    )

    try:
        result = run_ir_calibration(
            GPIO,
            sensor_pin=args.sensor_pin,
            emitter_pin=args.emitter_pin,
            duty=args.duty,
            duration=args.duration,
            interval=args.interval,
            settle=args.settle,
            recovery=args.recovery,
            start=args.start,
            stop=args.stop,
            step=args.step,
            sensitivity_delta=args.sensitivity_delta,
            hold_duration=args.hold_duration,
            saturation_gap=args.saturation_gap,
            freqs=args.freqs,
            skip_hold=args.skip_hold,
            preferred_frequency=args.preferred_frequency,
            pwm_backend=args.pwm_backend,
            pwm_chip=args.pwm_chip,
            pwm_channel=args.pwm_channel,
        )
    except CalibrationError as exc:
        print(f"ERRO: {exc}")
        return 1
    finally:
        GPIO.cleanup()

    if args.json:
        print(result_to_json(result))
        return 0

    print_stats("OFF", result["baseline"])
    for item in result["scan"]:
        print_stats(f"{item['freq']}Hz", item["stats"])

    if result["hold"]:
        print("\nTeste de saturacao com portadora continua:")
        for item in result["hold"]:
            print_hold_stats(
                f"{item['scan']['freq']}Hz",
                item["scan"]["signal_level_name"],
                item["hold"],
                args.hold_duration,
            )

    print_recommendation(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
