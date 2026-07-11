import time
import os
import threading
import logging
import importlib
import platform
import sys
import copy
import json
from datetime import datetime
from pathlib import Path

from .ir_calibration import (
    CalibrationError,
    HARDWARE_PWM_PINS,
    KernelSysfsPWM,
    calibration_result_is_valid,
    kernel_pwm_channel_for_pin,
    normalize_pwm_backend,
    run_ir_calibration,
)


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def _detect_raspberry_pi():
    for model_path in (
        Path("/proc/device-tree/model"),
        Path("/sys/firmware/devicetree/base/model"),
    ):
        try:
            model = model_path.read_text(errors="ignore").replace("\x00", "").strip()
        except OSError:
            continue

        if model:
            return "raspberry pi" in model.lower(), model

    machine = platform.machine()
    return False, machine


def _system_dist_package_paths():
    py_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = (
        Path("/usr/lib/python3/dist-packages"),
        Path("/usr/local/lib/python3/dist-packages"),
        Path("/usr/lib") / py_version / "dist-packages",
        Path("/usr/local/lib") / py_version / "dist-packages",
    )

    for candidate in candidates:
        if candidate.exists():
            yield candidate


def _import_optional_hardware_module(module_name):
    last_error = None

    try:
        return importlib.import_module(module_name), None, "python"
    except Exception as exc:
        last_error = exc

    # Venvs normally hide apt-installed Raspberry Pi packages. Try the system
    # dist-packages paths so python3-rpi.gpio/python3-pigpio can still be used.
    for package_path in _system_dist_package_paths():
        package_path_str = str(package_path)
        if package_path_str not in sys.path:
            sys.path.append(package_path_str)

        try:
            return importlib.import_module(module_name), None, package_path_str
        except Exception as exc:
            last_error = exc

    return None, f"{type(last_error).__name__}: {last_error}", None


IS_RASPBERRY_PI, RASPBERRY_PI_MODEL = _detect_raspberry_pi()
GPIO, GPIO_IMPORT_ERROR, GPIO_IMPORT_SOURCE = _import_optional_hardware_module("RPi.GPIO")
pigpio, PIGPIO_IMPORT_ERROR, PIGPIO_IMPORT_SOURCE = _import_optional_hardware_module("pigpio")

if GPIO is None:
    logging.warning(
        "RPi.GPIO indisponivel no Python %s. Hardware GPIO/IR nao sera inicializado. "
        "Ambiente: %s. Erro: %s",
        sys.executable,
        RASPBERRY_PI_MODEL,
        GPIO_IMPORT_ERROR,
    )
    if IS_RASPBERRY_PI:
        logging.warning(
            "Na Raspberry Pi, instale os pacotes no sistema ou recrie a venv com acesso a eles: "
            "sudo apt install python3-rpi.gpio. "
            "Para PWM por hardware sem pigpiod, habilite dtoverlay=pwm-2chan e use kernel_pwm."
        )
else:
    logging.info("RPi.GPIO carregado via %s.", GPIO_IMPORT_SOURCE)

if pigpio is None:
    logging.info(
        "pigpio indisponivel no Python %s. PWM do emissor IR usara RPi.GPIO se GPIO estiver disponivel. "
        "Erro: %s",
        sys.executable,
        PIGPIO_IMPORT_ERROR,
    )
else:
    logging.info("pigpio carregado via %s.", PIGPIO_IMPORT_SOURCE)

GPIO_PIN_DEFAULT = int(os.environ.get("AGILITY_GPIO_PIN", "17"))
IR_LED_PIN_DEFAULT = int(os.environ.get("AGILITY_IR_LED_PIN", "18"))
IR_FREQUENCY_DEFAULT = int(os.environ.get("AGILITY_IR_FREQUENCY", "50000"))
IR_DUTY_CYCLE_DEFAULT = float(os.environ.get("AGILITY_IR_DUTY_CYCLE", "50"))
IR_BURST_ON_DEFAULT = float(os.environ.get("AGILITY_IR_BURST_ON", "0.006"))
IR_BURST_OFF_DEFAULT = float(os.environ.get("AGILITY_IR_BURST_OFF", "0.014"))
IR_PWM_BACKEND_DEFAULT = os.environ.get("AGILITY_IR_PWM_BACKEND", "auto").strip().lower()
IR_PWM_CHIP_DEFAULT = int(os.environ.get("AGILITY_IR_PWM_CHIP", "0"))
IR_PWM_CHANNEL_ENV = os.environ.get("AGILITY_IR_PWM_CHANNEL")
IR_PWM_CHANNEL_DEFAULT = int(IR_PWM_CHANNEL_ENV) if IR_PWM_CHANNEL_ENV not in (None, "") else None
SENSOR_DEBOUNCE_DEFAULT = float(os.environ.get("AGILITY_SENSOR_DEBOUNCE", "1.0"))
SENSOR_REARM_STABLE_DEFAULT = float(os.environ.get("AGILITY_SENSOR_REARM_STABLE", "0.02"))
SENSOR_POLL_INTERVAL_DEFAULT = float(os.environ.get("AGILITY_SENSOR_POLL_INTERVAL", "0.001"))
SENSOR_ACTIVE_LEVEL_DEFAULT = os.environ.get("AGILITY_SENSOR_ACTIVE_LEVEL", "LOW").strip().upper()
SENSOR_READ_MODE_DEFAULT = os.environ.get("AGILITY_SENSOR_READ_MODE", "auto").strip().lower()
SENSOR_TRIGGER_CONFIRM_DEFAULT = float(os.environ.get("AGILITY_SENSOR_TRIGGER_CONFIRM", "0.002"))
SENSOR_SIGNAL_TIMEOUT_DEFAULT = float(os.environ.get("AGILITY_SENSOR_SIGNAL_TIMEOUT", "0.12"))
SENSOR_READY_CONFIRM_DEFAULT = float(os.environ.get("AGILITY_SENSOR_READY_CONFIRM", "0.05"))
SENSOR_READY_MIN_RATIO_DEFAULT = float(os.environ.get("AGILITY_SENSOR_READY_MIN_RATIO", "0.2"))
SENSOR_IGNORED_LOG_INTERVAL_DEFAULT = float(os.environ.get("AGILITY_SENSOR_IGNORED_LOG_INTERVAL", "2.0"))


def _bool_value(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _env_bool(name, default=True):
    return _bool_value(os.environ.get(name), default)


IR_EMITTER_ENABLED_DEFAULT = _env_bool("AGILITY_IR_EMITTER_ENABLED", True)
IR_BURST_ENABLED_DEFAULT = _env_bool("AGILITY_IR_BURST_ENABLED", True)
IR_CALIBRATE_ON_STARTUP_DEFAULT = _env_bool("AGILITY_IR_CALIBRATE_ON_STARTUP", False)
IR_CALIBRATION_APPLY_DEFAULT = _env_bool("AGILITY_IR_CALIBRATION_APPLY", True)
IR_CALIBRATION_SAVE_DEFAULT = _env_bool("AGILITY_IR_CALIBRATION_SAVE", True)
IR_USE_SAVED_CALIBRATION_DEFAULT = _env_bool("AGILITY_IR_USE_SAVED_CALIBRATION", True)
IR_CALIBRATION_START_DEFAULT = int(os.environ.get("AGILITY_IR_CALIBRATION_START", "10000"))
IR_CALIBRATION_STOP_DEFAULT = int(os.environ.get("AGILITY_IR_CALIBRATION_STOP", "60000"))
IR_CALIBRATION_STEP_DEFAULT = int(os.environ.get("AGILITY_IR_CALIBRATION_STEP", "1000"))
IR_CALIBRATION_DURATION_DEFAULT = float(os.environ.get("AGILITY_IR_CALIBRATION_DURATION", "0.35"))
IR_CALIBRATION_SETTLE_DEFAULT = float(os.environ.get("AGILITY_IR_CALIBRATION_SETTLE", "0.08"))
IR_CALIBRATION_RECOVERY_DEFAULT = float(os.environ.get("AGILITY_IR_CALIBRATION_RECOVERY", "1.0"))
IR_CALIBRATION_HOLD_DEFAULT = float(os.environ.get("AGILITY_IR_CALIBRATION_HOLD", "1.0"))
IR_CALIBRATION_SENSITIVITY_DELTA_DEFAULT = float(
    os.environ.get("AGILITY_IR_CALIBRATION_SENSITIVITY_DELTA", "25")
)
IR_CALIBRATION_SATURATION_GAP_DEFAULT = float(
    os.environ.get("AGILITY_IR_CALIBRATION_SATURATION_GAP", "0.05")
)
IR_CALIBRATION_PREFERRED_FREQUENCY_DEFAULT = int(
    os.environ.get("AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY", str(IR_FREQUENCY_DEFAULT))
)
IR_CALIBRATION_PREFERENCE_TOLERANCE_DEFAULT = float(
    os.environ.get("AGILITY_IR_CALIBRATION_PREFERENCE_TOLERANCE", "1.0")
)
SENSOR_REQUIRE_REARM_DEFAULT = _env_bool("AGILITY_SENSOR_REQUIRE_REARM", False)
SENSOR_REQUIRE_READY_DEFAULT = _env_bool("AGILITY_SENSOR_REQUIRE_READY", True)


class Chronometer:
    """Cronômetro de prova de Agility com dois tempos:
    - TIA (Tempo de Início Autorizado): da autorização ao 1o acionamento do sensor/botão
    - TOP (Tempo Oficial da Prova): do 1o ao 2o acionamento do sensor/botão
    """

    def __init__(
        self,
        ir_pin=None,
        debounce_time=SENSOR_DEBOUNCE_DEFAULT,
        ir_led_pin=None,
        ir_frequency=IR_FREQUENCY_DEFAULT,
        ir_duty_cycle=IR_DUTY_CYCLE_DEFAULT,
        ir_pwm_backend=IR_PWM_BACKEND_DEFAULT,
        ir_pwm_chip=IR_PWM_CHIP_DEFAULT,
        ir_pwm_channel=IR_PWM_CHANNEL_DEFAULT,
        ir_emitter_enabled=IR_EMITTER_ENABLED_DEFAULT,
        ir_burst_enabled=IR_BURST_ENABLED_DEFAULT,
        ir_burst_on_time=IR_BURST_ON_DEFAULT,
        ir_burst_off_time=IR_BURST_OFF_DEFAULT,
        sensor_poll_interval=SENSOR_POLL_INTERVAL_DEFAULT,
        sensor_rearm_stable_time=SENSOR_REARM_STABLE_DEFAULT,
        sensor_active_level=SENSOR_ACTIVE_LEVEL_DEFAULT,
        sensor_require_rearm=SENSOR_REQUIRE_REARM_DEFAULT,
        sensor_read_mode=SENSOR_READ_MODE_DEFAULT,
        sensor_require_ready=SENSOR_REQUIRE_READY_DEFAULT,
        sensor_trigger_confirm_time=SENSOR_TRIGGER_CONFIRM_DEFAULT,
        sensor_signal_timeout=SENSOR_SIGNAL_TIMEOUT_DEFAULT,
        sensor_ready_confirm_time=SENSOR_READY_CONFIRM_DEFAULT,
        sensor_ready_min_ratio=SENSOR_READY_MIN_RATIO_DEFAULT,
        sensor_ignored_log_interval=SENSOR_IGNORED_LOG_INTERVAL_DEFAULT,
    ):
        self.ir_pin = ir_pin if ir_pin is not None else GPIO_PIN_DEFAULT
        self.ir_led_pin = ir_led_pin if ir_led_pin is not None else IR_LED_PIN_DEFAULT
        self.ir_frequency = ir_frequency
        self.ir_duty_cycle = ir_duty_cycle
        self.ir_pwm_backend = normalize_pwm_backend(ir_pwm_backend)
        self.ir_pwm_chip = int(ir_pwm_chip)
        self.ir_pwm_channel = ir_pwm_channel
        self.ir_emitter_enabled = ir_emitter_enabled
        self.ir_burst_enabled = _bool_value(ir_burst_enabled, True)
        self.ir_burst_on_time = max(0.0005, float(ir_burst_on_time))
        self.ir_burst_off_time = max(0.0005, float(ir_burst_off_time))
        self.sensor_poll_interval = max(0.001, float(sensor_poll_interval))
        self.sensor_rearm_stable_time = max(0.001, float(sensor_rearm_stable_time))
        self.sensor_trigger_confirm_time = max(0.0, float(sensor_trigger_confirm_time))
        self.sensor_signal_timeout = max(self.sensor_poll_interval, float(sensor_signal_timeout))
        self.sensor_ready_confirm_time = max(0.0, float(sensor_ready_confirm_time))
        self.sensor_ready_min_ratio = max(0.0, min(1.0, float(sensor_ready_min_ratio)))
        self.sensor_require_rearm = _bool_value(sensor_require_rearm, False)
        self.sensor_require_ready = _bool_value(sensor_require_ready, True)
        self.sensor_read_mode = str(sensor_read_mode).strip().lower()
        if self.sensor_read_mode not in ("polling", "event_detect", "auto"):
            logging.warning(
                "AGILITY_SENSOR_READ_MODE invalido: %s. Usando polling.",
                self.sensor_read_mode,
            )
            self.sensor_read_mode = "polling"
        self.sensor_active_level = str(sensor_active_level).strip().upper()
        if self.sensor_active_level not in ("LOW", "HIGH"):
            logging.warning(
                "AGILITY_SENSOR_ACTIVE_LEVEL invalido: %s. Usando LOW.",
                self.sensor_active_level,
            )
            self.sensor_active_level = "LOW"
        self.sensor_ignored_log_interval = max(0.1, float(sensor_ignored_log_interval))
        self._ir_pwm = None
        self._ir_burst_thread = None
        self._ir_burst_stop = threading.Event()
        self._pigpio = None
        self._kernel_pwm = None
        self._using_pigpio_pwm = False
        self._using_kernel_pwm = False
        self._gpio_ready = False
        self._ir_emitter_active = False
        self._ir_emitter_mode = None
        self._ir_emitter_error = None
        self._gpio_error = None
        self._sensor_mode = None
        self._sensor_error = None
        self._sensor_fallback_reason = None
        self._sensor_poll_stop = threading.Event()
        self._sensor_poll_thread = None
        self._last_sensor_level = None
        self._sensor_high_since = None
        self._sensor_armed = True
        self._sensor_ignored_count = 0
        self._sensor_last_ignored_reason = None
        self._sensor_last_ignore_log = 0
        self._sensor_accepted_count = 0
        self._sensor_last_event = None
        self._sensor_last_accepted_event = None
        self._sensor_transition_count = 0
        self._sensor_last_transition = None
        self._sensor_last_ready_check = None
        self._beam_aligned = False
        self._beam_last_signal_at = None
        self._beam_last_break_at = None
        self._beam_break_count = 0
        self._last_authorize_error = None
        self._calibration_lock = threading.Lock()
        self._calibration_store_path = Path(
            os.environ.get(
                "AGILITY_IR_CALIBRATION_FILE",
                str(Path(__file__).resolve().parent.parent / "ir_calibration.json"),
            )
        )
        self._calibration_last_result = self._load_ir_calibration_file()
        self._calibration_running = False
        self._calibration_status = {
            "running": False,
            "phase": None,
            "frequency_hz": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "last_attempt": None,
            "last_result": self._calibration_last_result,
            "store_path": str(self._calibration_store_path),
        }
        if IR_USE_SAVED_CALIBRATION_DEFAULT:
            self._apply_saved_ir_calibration()
        self._ensure_signal_timeout_safe()
        self.debounce_time = max(0.001, float(debounce_time))
        self._lock = threading.RLock()
        self._last_ir_trigger = 0
        self._state_change_listeners = []

        # Estado
        self._estado = "idle"
        self._id_inscricao = None
        self._dados_inscricao = {}

        # Timestamps perf_counter (fonte oficial de tempo)
        self._t_autorizado = None
        self._t_inicio_prova = None
        self._t_fim_prova = None

        # Timestamps wall-clock (para persistência no banco)
        self._hora_autorizacao = None
        self._hora_inicio_prova = None
        self._hora_fim_prova = None
        self._state_version = 0
        self._updated_at = datetime.now().isoformat()

        # Contadores
        self._faltas = 0
        self._recusas = 0

        # GPIO
        if not GPIO:
            self._gpio_error = f"RPi.GPIO indisponivel: {GPIO_IMPORT_ERROR}"
            self._ir_emitter_error = self._gpio_error
            logging.warning(
                "Cronometro iniciado em modo sem GPIO. Sensor e emissor IR inativos. "
                "Python em uso: %s",
                sys.executable,
            )
        elif not self.ir_pin:
            self._gpio_error = "pino do sensor IR nao configurado"
            logging.warning("Pino do sensor IR nao configurado. GPIO nao inicializado.")
        else:
            try:
                GPIO.setwarnings(False)
                GPIO.setmode(GPIO.BCM)

                if self.ir_emitter_enabled and self.ir_led_pin == self.ir_pin:
                    logging.warning("GPIO do emissor IR igual ao sensor. Emissor desabilitado.")
                    self.ir_emitter_enabled = False

                self._start_ir_emitter()
                self._start_sensor_input()
            except Exception as exc:
                self._gpio_ready = False
                self._gpio_error = f"{type(exc).__name__}: {exc}"
                logging.exception("Falha ao inicializar GPIO do cronometro.")

    def _ensure_signal_timeout_safe(self):
        if not self.ir_burst_enabled:
            return

        min_signal_timeout = max(
            self.sensor_poll_interval * 3,
            (self.ir_burst_on_time + self.ir_burst_off_time) * 3,
        )
        if self.sensor_signal_timeout < min_signal_timeout:
            logging.warning(
                "AGILITY_SENSOR_SIGNAL_TIMEOUT %.3fs menor que o minimo seguro %.3fs "
                "para rajadas IR. Ajustando automaticamente.",
                self.sensor_signal_timeout,
                min_signal_timeout,
            )
            self.sensor_signal_timeout = min_signal_timeout

    def _load_ir_calibration_file(self):
        try:
            if not self._calibration_store_path.is_file():
                return None
            with self._calibration_store_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            if not isinstance(data, dict):
                logging.warning(
                    "Arquivo de calibracao IR invalido em %s: conteudo nao e objeto JSON.",
                    self._calibration_store_path,
                )
                return None
            if not calibration_result_is_valid(data):
                logging.warning(
                    "Arquivo de calibracao IR ignorado em %s: recomendacao ausente "
                    "ou timeout fora do limite de 0.120s.",
                    self._calibration_store_path,
                )
                return None
            return data
        except Exception as exc:
            logging.warning(
                "Nao foi possivel ler calibracao IR salva em %s: %s",
                self._calibration_store_path,
                exc,
            )
            return None

    def _save_ir_calibration_file(self, result):
        self._calibration_store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._calibration_store_path.open("w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=2, sort_keys=True)

    def _apply_saved_ir_calibration(self):
        if not self._calibration_last_result:
            return

        recommendation = self._calibration_last_result.get("recommendation")
        if not recommendation:
            return

        self._apply_ir_recommendation(
            recommendation,
            override_env=False,
            source="arquivo de calibracao salvo",
        )

    def _apply_ir_recommendation(self, recommendation, override_env=True, source="calibracao"):
        if not recommendation:
            return False

        def should_apply(env_name):
            return override_env or env_name not in os.environ

        if should_apply("AGILITY_IR_FREQUENCY") and recommendation.get("frequency_hz"):
            self.ir_frequency = int(recommendation["frequency_hz"])
        if should_apply("AGILITY_IR_DUTY_CYCLE") and recommendation.get("duty_cycle") is not None:
            self.ir_duty_cycle = float(recommendation["duty_cycle"])
        if should_apply("AGILITY_IR_BURST_ENABLED") and recommendation.get("burst_enabled") is not None:
            self.ir_burst_enabled = _bool_value(recommendation["burst_enabled"], True)
        if should_apply("AGILITY_IR_BURST_ON") and recommendation.get("burst_on") is not None:
            self.ir_burst_on_time = max(0.0005, float(recommendation["burst_on"]))
        if should_apply("AGILITY_IR_BURST_OFF") and recommendation.get("burst_off") is not None:
            self.ir_burst_off_time = max(0.0005, float(recommendation["burst_off"]))
        if (
            should_apply("AGILITY_SENSOR_ACTIVE_LEVEL")
            and recommendation.get("sensor_active_level") in ("LOW", "HIGH")
        ):
            self.sensor_active_level = recommendation["sensor_active_level"]
        if (
            should_apply("AGILITY_SENSOR_SIGNAL_TIMEOUT")
            and recommendation.get("sensor_signal_timeout") is not None
        ):
            self.sensor_signal_timeout = max(
                self.sensor_poll_interval,
                float(recommendation["sensor_signal_timeout"]),
            )
        if (
            should_apply("AGILITY_SENSOR_TRIGGER_CONFIRM")
            and recommendation.get("sensor_trigger_confirm") is not None
        ):
            self.sensor_trigger_confirm_time = max(
                0.0,
                float(recommendation["sensor_trigger_confirm"]),
            )
        if (
            should_apply("AGILITY_SENSOR_READY_MIN_RATIO")
            and recommendation.get("sensor_ready_min_ratio") is not None
        ):
            self.sensor_ready_min_ratio = max(
                0.0,
                min(1.0, float(recommendation["sensor_ready_min_ratio"])),
            )

        self._ensure_signal_timeout_safe()
        logging.info(
            "Configuracao IR aplicada via %s: %sHz, duty %.1f%%, rajada %.4fs/%.4fs, "
            "nivel ativo %s.",
            source,
            self.ir_frequency,
            self.ir_duty_cycle,
            self.ir_burst_on_time,
            self.ir_burst_off_time,
            self.sensor_active_level,
        )
        return True

    def _start_sensor_input(self):
        GPIO.setup(self.ir_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._last_sensor_level = GPIO.input(self.ir_pin)
        self._sensor_armed = self._last_sensor_level == self._sensor_inactive_level()
        self._sensor_high_since = time.perf_counter() if self._sensor_armed else None
        now = time.perf_counter()
        if self._last_sensor_level == self._sensor_inactive_level():
            self._beam_last_signal_at = now
            self._beam_aligned = True
        logging.info(
            "Sensor IR configurado no GPIO %s com pull-up interno. Nivel inicial: %s. "
            "Estado do feixe: %s. Nivel ativo/quebrado: %s. Armado: %s. "
            "Rearme obrigatorio: %s. Leitura: %s. Timeout sinal: %.3fs.",
            self.ir_pin,
            self._last_sensor_level,
            self._sensor_level_status(self._last_sensor_level),
            self.sensor_active_level,
            self._sensor_armed,
            self.sensor_require_rearm,
            self.sensor_read_mode,
            self.sensor_signal_timeout,
        )

        if self.ir_burst_enabled and self.sensor_read_mode != "polling":
            logging.info(
                "Rajadas IR habilitadas. Usando polling logico para detectar ausencia de pulsos."
            )
            self._start_sensor_polling(configured=True)
            return

        if self.sensor_read_mode == "polling":
            self._start_sensor_polling(configured=True)
            return

        try:
            GPIO.add_event_detect(
                self.ir_pin,
                self._sensor_edge(),
                callback=self._ir_callback,
                bouncetime=int(self.debounce_time * 1000),
            )
            self._sensor_mode = "event_detect"
            self._sensor_error = None
            self._sensor_fallback_reason = None
            self._gpio_ready = True
            self._gpio_error = None
            logging.info(
                "Sensor IR usando interrupcao RPi.GPIO.add_event_detect no GPIO %s. "
                "Se quiser seguir exatamente o loop do tutorial, use AGILITY_SENSOR_READ_MODE=polling.",
                self.ir_pin,
            )
            self._start_sensor_monitor_thread()
        except Exception as exc:
            self._sensor_fallback_reason = f"{type(exc).__name__}: {exc}"
            self._sensor_error = None
            logging.warning(
                "Falha ao registrar interrupcao no GPIO %s. "
                "Motivo: %s. "
                "Possiveis causas: outro processo usando o pino, backend duplicado, "
                "permissao de GPIO ou limitacao da versao do RPi.GPIO/kernel. "
                "Ativando fallback por polling.",
                self.ir_pin,
                self._sensor_fallback_reason,
            )
            self._start_sensor_polling(configured=False)

    def _start_sensor_polling(self, configured=False):
        self._sensor_mode = "polling"
        self._gpio_ready = True
        self._gpio_error = None
        self._start_sensor_monitor_thread()
        log = logging.info if configured else logging.warning
        log(
            "Sensor IR usando polling no GPIO %s a cada %.3fs. Modo equivalente ao loop do tutorial.",
            self.ir_pin,
            self.sensor_poll_interval,
        )

    def _start_sensor_monitor_thread(self):
        if self._sensor_poll_thread and self._sensor_poll_thread.is_alive():
            return

        self._sensor_poll_stop.clear()
        self._sensor_poll_thread = threading.Thread(
            target=self._sensor_poll_loop,
            name="agility-gpio17-sensor",
            daemon=True,
        )
        self._sensor_poll_thread.start()

    def _sensor_poll_loop(self):
        while not self._sensor_poll_stop.is_set():
            try:
                current_level = GPIO.input(self.ir_pin)
                now = time.perf_counter()
                with self._lock:
                    previous_level = self._last_sensor_level
                    self._refresh_sensor_level_locked(current_level, now)
                    should_trigger = self._sensor_mode == "polling" and self._update_beam_state_locked(
                        previous_level,
                        current_level,
                        now,
                    )
                    trigger_time = self._beam_last_break_at if should_trigger else None

                if should_trigger:
                    self._ir_callback(self.ir_pin, event_time=trigger_time)
            except Exception as exc:
                self._gpio_ready = False
                self._gpio_error = f"{type(exc).__name__}: {exc}"
                self._sensor_error = self._gpio_error
                logging.exception("Falha ao ler GPIO %s em modo polling.", self.ir_pin)
                return

            self._sensor_poll_stop.wait(self.sensor_poll_interval)

    def _update_sensor_rearm_locked(self, current_level, now):
        if current_level == self._sensor_inactive_level():
            if self._sensor_high_since is None:
                self._sensor_high_since = now

            stable_for = now - self._sensor_high_since
            if not self._sensor_armed and (
                not self.sensor_require_rearm
                or stable_for >= self.sensor_rearm_stable_time
            ):
                self._sensor_armed = True
                self._sensor_last_ignored_reason = None
                if self.sensor_require_rearm:
                    logging.info(
                        "Sensor IR rearmado depois de %.3fs no nivel inativo.",
                        stable_for,
                    )
        else:
            self._sensor_high_since = None
            if not self.sensor_require_rearm:
                self._sensor_armed = False

    def _sensor_active_level(self):
        return self._gpio_high() if self.sensor_active_level == "HIGH" else self._gpio_low()

    def _sensor_inactive_level(self):
        return self._gpio_low() if self.sensor_active_level == "HIGH" else self._gpio_high()

    def _gpio_high(self):
        return GPIO.HIGH if GPIO is not None else 1

    def _gpio_low(self):
        return GPIO.LOW if GPIO is not None else 0

    def _sensor_level_status(self, level):
        if level is None:
            return "desconhecido"
        if level == self._sensor_active_level():
            return "feixe_quebrado"
        if level == self._sensor_inactive_level():
            return "feixe_alinhado"
        return f"nivel_{level}"

    def _logical_beam_status_locked(self):
        return "feixe_alinhado" if self._beam_aligned else "feixe_quebrado"

    def _update_beam_state_locked(self, previous_level, current_level, now):
        was_aligned = self._beam_aligned

        if not self.ir_burst_enabled:
            self._beam_aligned = current_level == self._sensor_inactive_level()
            if self._beam_aligned:
                self._beam_last_signal_at = now

            if was_aligned and not self._beam_aligned:
                self._beam_last_break_at = now
                self._beam_break_count += 1
                return True

            return False

        if current_level == self._sensor_inactive_level():
            self._beam_last_signal_at = now
            self._beam_aligned = True
        elif self._beam_last_signal_at is None:
            self._beam_aligned = False
        elif now - self._beam_last_signal_at >= self.sensor_signal_timeout:
            self._beam_aligned = False

        if was_aligned and not self._beam_aligned:
            expected_next_signal_at = (
                self._beam_last_signal_at + self.ir_burst_on_time + self.ir_burst_off_time
                if self._beam_last_signal_at is not None
                else now
            )
            self._beam_last_break_at = min(now, expected_next_signal_at)
            self._beam_break_count += 1
            return True

        return False

    def _sensor_ready_to_start_locked(self, now=None):
        if not self._gpio_ready or self._last_sensor_level is None:
            self._sensor_last_ready_check = None
            return True

        if self.ir_burst_enabled:
            ready_stats = self._sample_logical_beam_locked(
                duration=self.sensor_ready_confirm_time,
            )
            self._sensor_last_ready_check = ready_stats
            if ready_stats is None:
                return self._beam_aligned
            return ready_stats["expected_ratio"] >= self.sensor_ready_min_ratio

        ready_stats = self._sample_sensor_level_locked(
            expected_level=self._sensor_inactive_level(),
            duration=self.sensor_ready_confirm_time,
        )
        self._sensor_last_ready_check = ready_stats
        if ready_stats is None:
            return self._last_sensor_level == self._sensor_inactive_level()
        return ready_stats["expected_ratio"] >= self.sensor_ready_min_ratio

    def _sensor_edge(self):
        return GPIO.RISING if self.sensor_active_level == "HIGH" else GPIO.FALLING

    def _record_sensor_transition_locked(self, previous_level, current_level):
        self._sensor_transition_count += 1
        self._sensor_last_transition = {
            "de": previous_level,
            "para": current_level,
            "ativo": current_level == self._sensor_active_level(),
            "estado_sinal": self._sensor_level_status(current_level),
            "hora": datetime.now().isoformat(),
            "modo": self._sensor_mode,
        }

    def _refresh_sensor_level_locked(self, current_level, now):
        previous_level = self._last_sensor_level
        self._last_sensor_level = current_level
        if previous_level is not None and current_level != previous_level:
            self._record_sensor_transition_locked(previous_level, current_level)
        self._update_sensor_rearm_locked(current_level, now)

    def _sample_logical_beam_locked(self, duration):
        if GPIO is None or not self._gpio_ready:
            return None

        samples = 0
        aligned_samples = 0
        raw_high_samples = 0
        raw_low_samples = 0
        transitions = 0
        logical_breaks = 0
        first_level = None
        first_aligned = None
        last_aligned = None
        previous_level = self._last_sensor_level
        previous_aligned = self._beam_aligned
        deadline = time.perf_counter() + duration
        interval = max(0.001, min(self.sensor_poll_interval, 0.005))

        while True:
            current_level = GPIO.input(self.ir_pin)
            now = time.perf_counter()
            self._refresh_sensor_level_locked(current_level, now)
            self._update_beam_state_locked(previous_level, current_level, now)

            if first_level is None:
                first_level = current_level
                first_aligned = self._beam_aligned
            if previous_level is not None and current_level != previous_level:
                transitions += 1
            if previous_aligned and not self._beam_aligned:
                logical_breaks += 1

            previous_level = current_level
            previous_aligned = self._beam_aligned
            last_aligned = self._beam_aligned

            samples += 1
            if self._beam_aligned:
                aligned_samples += 1
            if current_level == self._gpio_high():
                raw_high_samples += 1
            else:
                raw_low_samples += 1

            if now >= deadline:
                break
            time.sleep(interval)

        expected_ratio = aligned_samples / samples if samples else 0
        return {
            "expected_level": "feixe_logico_alinhado",
            "expected_ratio": round(expected_ratio, 3),
            "samples": samples,
            "signal_samples": aligned_samples,
            "high": raw_high_samples,
            "low": raw_low_samples,
            "transitions": transitions,
            "logical_breaks": logical_breaks,
            "first": first_level,
            "last": previous_level,
            "first_aligned": first_aligned,
            "last_aligned": last_aligned,
            "duration": duration,
            "min_ratio": self.sensor_ready_min_ratio,
            "ultimo_sinal_atras_s": (
                round(time.perf_counter() - self._beam_last_signal_at, 3)
                if self._beam_last_signal_at is not None
                else None
            ),
            "hora": datetime.now().isoformat(),
        }

    def _sample_sensor_level_locked(self, expected_level, duration):
        if GPIO is None or not self._gpio_ready:
            return None

        samples = 0
        expected_samples = 0
        signal_samples = 0
        high_samples = 0
        low_samples = 0
        transitions = 0
        first_level = None
        previous_level = self._last_sensor_level
        deadline = time.perf_counter() + duration
        interval = max(0.001, min(self.sensor_poll_interval, 0.005))

        while True:
            current_level = GPIO.input(self.ir_pin)
            now = time.perf_counter()
            self._refresh_sensor_level_locked(current_level, now)

            if first_level is None:
                first_level = current_level
            if previous_level is not None and current_level != previous_level:
                transitions += 1
            previous_level = current_level

            samples += 1
            if current_level == expected_level:
                expected_samples += 1
                signal_samples += 1
            if current_level == self._gpio_high():
                high_samples += 1
            else:
                low_samples += 1

            if now >= deadline:
                break
            time.sleep(interval)

        expected_ratio = expected_samples / samples if samples else 0
        return {
            "expected_level": expected_level,
            "expected_ratio": round(expected_ratio, 3),
            "samples": samples,
            "signal_samples": signal_samples,
            "high": high_samples,
            "low": low_samples,
            "transitions": transitions,
            "first": first_level,
            "last": previous_level,
            "duration": duration,
            "min_ratio": self.sensor_ready_min_ratio,
            "hora": datetime.now().isoformat(),
        }

    def _start_ir_emitter(self):
        if not GPIO:
            self._ir_emitter_error = "RPi.GPIO indisponivel"
            logging.warning("Emissor IR nao inicializado: RPi.GPIO indisponivel.")
            return

        if not self.ir_emitter_enabled:
            self._ir_emitter_error = "emissor desabilitado por configuracao"
            logging.warning("Emissor IR desabilitado por configuracao.")
            return

        if not self.ir_led_pin:
            self._ir_emitter_error = "pino do emissor nao configurado"
            logging.warning("Emissor IR nao inicializado: pino nao configurado.")
            return

        duty_cycle = max(0, min(100, self.ir_duty_cycle))
        self._using_pigpio_pwm = False
        self._using_kernel_pwm = False
        self._kernel_pwm = None
        self._ir_pwm = None

        if self.ir_pwm_backend in ("auto", "kernel_pwm"):
            try:
                if self.ir_led_pin not in HARDWARE_PWM_PINS:
                    raise RuntimeError(
                        f"GPIO{self.ir_led_pin} nao suporta PWM de hardware; use {sorted(HARDWARE_PWM_PINS)}"
                    )
                self._kernel_pwm = KernelSysfsPWM(
                    self.ir_led_pin,
                    chip=self.ir_pwm_chip,
                    channel=self.ir_pwm_channel,
                )
                self._kernel_pwm.setup()
                if not self.ir_burst_enabled:
                    self._kernel_pwm.start(self.ir_frequency, duty_cycle)
                self._using_kernel_pwm = True
                self._ir_emitter_active = True
                self._ir_emitter_mode = (
                    "kernel.sysfs.PWM.burst" if self.ir_burst_enabled else "kernel.sysfs.PWM"
                )
                self._ir_emitter_error = None
                logging.info(
                    "LED IR em GPIO %s com PWM de hardware do kernel %sHz e duty %.1f%% "
                    "(pwmchip%s/pwm%s).",
                    self.ir_led_pin,
                    self.ir_frequency,
                    duty_cycle,
                    self.ir_pwm_chip,
                    kernel_pwm_channel_for_pin(self.ir_led_pin, self.ir_pwm_channel),
                )
                if self.ir_burst_enabled:
                    self._start_ir_burst_thread(duty_cycle)
                return
            except Exception as exc:
                self._kernel_pwm = None
                if self.ir_pwm_backend == "kernel_pwm":
                    self._ir_emitter_active = False
                    self._ir_emitter_mode = "kernel.sysfs.PWM"
                    self._ir_emitter_error = f"{type(exc).__name__}: {exc}"
                    logging.error(
                        "Emissor IR nao inicializado: AGILITY_IR_PWM_BACKEND=kernel_pwm, "
                        "mas PWM do kernel falhou: %s",
                        exc,
                    )
                    return
                logging.warning("Falha ao iniciar PWM do kernel: %s. Tentando pigpio/RPi.GPIO.", exc)

        if self.ir_pwm_backend in ("auto", "pigpio"):
            try:
                if pigpio is None:
                    raise RuntimeError(f"pigpio indisponivel: {PIGPIO_IMPORT_ERROR}")
                if self.ir_led_pin not in HARDWARE_PWM_PINS:
                    raise RuntimeError(
                        f"GPIO{self.ir_led_pin} nao suporta hardware_PWM; use {sorted(HARDWARE_PWM_PINS)}"
                    )
                self._pigpio = pigpio.pi()
                if self._pigpio.connected:
                    self._pigpio.set_mode(self.ir_led_pin, pigpio.OUTPUT)
                    self._pigpio.hardware_PWM(
                        self.ir_led_pin,
                        self.ir_frequency if not self.ir_burst_enabled else 0,
                        int(duty_cycle * 10000) if not self.ir_burst_enabled else 0,
                    )
                    self._using_pigpio_pwm = True
                    self._ir_emitter_active = True
                    self._ir_emitter_mode = (
                        "pigpio.hardware_PWM.burst"
                        if self.ir_burst_enabled
                        else "pigpio.hardware_PWM"
                    )
                    self._ir_emitter_error = None
                    logging.info(
                        f"LED IR em GPIO {self.ir_led_pin} com PWM hardware "
                        f"{self.ir_frequency}Hz e duty {duty_cycle}%."
                    )
                    if self.ir_burst_enabled:
                        self._start_ir_burst_thread(duty_cycle)
                    return

                self._pigpio = None
                raise RuntimeError("pigpio importado, mas daemon pigpiod nao conectado")
            except Exception as exc:
                self._pigpio = None
                if self.ir_pwm_backend == "pigpio":
                    self._ir_emitter_active = False
                    self._ir_emitter_mode = "pigpio.hardware_PWM"
                    self._ir_emitter_error = f"{type(exc).__name__}: {exc}"
                    logging.error(
                        "Emissor IR nao inicializado: AGILITY_IR_PWM_BACKEND=pigpio, "
                        "mas pigpio.hardware_PWM falhou: %s",
                        exc,
                    )
                    return
                logging.warning("Falha ao iniciar PWM via pigpio: %s. Usando PWM via RPi.GPIO.", exc)

        try:
            GPIO.setup(self.ir_led_pin, GPIO.OUT, initial=GPIO.LOW)
            self._ir_pwm = GPIO.PWM(self.ir_led_pin, self.ir_frequency)
            self._ir_pwm.start(0 if self.ir_burst_enabled else duty_cycle)
            self._ir_emitter_active = True
            self._ir_emitter_mode = (
                "RPi.GPIO.PWM.burst" if self.ir_burst_enabled else "RPi.GPIO.PWM"
            )
            self._ir_emitter_error = None
            logging.info(
                f"LED IR em GPIO {self.ir_led_pin} com PWM {self.ir_frequency}Hz "
                f"e duty {duty_cycle}%."
            )
            if self.ir_burst_enabled:
                self._start_ir_burst_thread(duty_cycle)
        except Exception as exc:
            self._ir_emitter_active = False
            self._ir_emitter_error = f"{type(exc).__name__}: {exc}"
            logging.exception("Falha ao inicializar emissor IR.")

    def _set_ir_carrier_active(self, active, duty_cycle):
        if self._using_kernel_pwm and self._kernel_pwm is not None:
            if active:
                self._kernel_pwm.start(self.ir_frequency, duty_cycle)
            else:
                self._kernel_pwm.off()
            return

        if self._using_pigpio_pwm and self._pigpio is not None:
            self._pigpio.hardware_PWM(
                self.ir_led_pin,
                self.ir_frequency if active else 0,
                int(duty_cycle * 10000) if active else 0,
            )
            return

        if self._ir_pwm is not None:
            self._ir_pwm.ChangeDutyCycle(duty_cycle if active else 0)

    def _start_ir_burst_thread(self, duty_cycle):
        if self._ir_burst_thread and self._ir_burst_thread.is_alive():
            return

        self._ir_burst_stop.clear()
        self._ir_burst_thread = threading.Thread(
            target=self._ir_burst_loop,
            args=(duty_cycle,),
            name="agility-ir-burst",
            daemon=True,
        )
        self._ir_burst_thread.start()
        logging.info(
            "Emissor IR em rajadas: %.3fs ligado / %.3fs desligado.",
            self.ir_burst_on_time,
            self.ir_burst_off_time,
        )

    def _ir_burst_loop(self, duty_cycle):
        while not self._ir_burst_stop.is_set():
            self._set_ir_carrier_active(True, duty_cycle)
            if self._ir_burst_stop.wait(self.ir_burst_on_time):
                break
            self._set_ir_carrier_active(False, duty_cycle)
            self._ir_burst_stop.wait(self.ir_burst_off_time)

        self._set_ir_carrier_active(False, duty_cycle)

    def _stop_ir_burst_thread(self):
        if self._ir_burst_thread and self._ir_burst_thread.is_alive():
            self._ir_burst_stop.set()
            self._ir_burst_thread.join(timeout=1)
        self._ir_burst_thread = None
        self._ir_burst_stop.clear()

    def _stop_ir_emitter(self):
        self._stop_ir_burst_thread()

        if self._using_kernel_pwm and self._kernel_pwm is not None:
            try:
                self._kernel_pwm.cleanup()
            except Exception:
                logging.exception("Falha ao desligar PWM do kernel do emissor IR.")

        if self._using_pigpio_pwm and self._pigpio is not None:
            try:
                self._pigpio.hardware_PWM(self.ir_led_pin, 0, 0)
            except Exception:
                logging.exception("Falha ao desligar PWM pigpio do emissor IR.")
            try:
                self._pigpio.stop()
            except Exception:
                logging.exception("Falha ao encerrar conexao pigpio do emissor IR.")

        if self._ir_pwm is not None:
            try:
                self._ir_pwm.stop()
            except Exception:
                logging.exception("Falha ao parar PWM RPi.GPIO do emissor IR.")

        if GPIO is not None and self.ir_led_pin:
            try:
                GPIO.output(self.ir_led_pin, GPIO.LOW)
            except Exception:
                pass

        self._pigpio = None
        self._kernel_pwm = None
        self._using_pigpio_pwm = False
        self._using_kernel_pwm = False
        self._ir_pwm = None
        self._ir_emitter_active = False

    def _stop_sensor_input(self):
        if self._sensor_poll_thread and self._sensor_poll_thread.is_alive():
            self._sensor_poll_stop.set()
            self._sensor_poll_thread.join(timeout=1)
        self._sensor_poll_thread = None
        self._sensor_poll_stop.clear()

        if GPIO is not None and self._sensor_mode == "event_detect":
            try:
                GPIO.remove_event_detect(self.ir_pin)
            except Exception:
                pass

        self._sensor_mode = None
        self._gpio_ready = False

    def _restart_gpio_processing(self):
        if GPIO is None or not self.ir_pin:
            return
        try:
            self._start_ir_emitter()
            self._start_sensor_input()
        except Exception as exc:
            self._gpio_ready = False
            self._gpio_error = f"{type(exc).__name__}: {exc}"
            logging.exception("Falha ao reiniciar GPIO apos calibracao IR.")

    def _ir_callback(self, channel, event_time=None):
        trigger_time = event_time if event_time is not None else time.perf_counter()
        confirmed_level = None
        confirmed_at = trigger_time
        if channel is not None and self.sensor_trigger_confirm_time > 0 and GPIO is not None:
            time.sleep(self.sensor_trigger_confirm_time)
            confirmed_level = GPIO.input(self.ir_pin)
            confirmed_at = time.perf_counter()

        with self._lock:
            if confirmed_level is not None:
                previous_level = self._last_sensor_level
                self._refresh_sensor_level_locked(confirmed_level, confirmed_at)
                self._update_beam_state_locked(previous_level, confirmed_level, confirmed_at)

                if self.ir_burst_enabled:
                    if self._beam_aligned:
                        self._ignore_sensor_trigger_locked(
                            "pulso descartado: feixe logico voltou antes da confirmacao",
                            channel,
                        )
                        return
                elif confirmed_level != self._sensor_active_level():
                    self._ignore_sensor_trigger_locked(
                        "pulso descartado: nivel ativo nao permaneceu estavel",
                        channel,
                    )
                    return

            if trigger_time - self._last_ir_trigger < self.debounce_time:
                self._ignore_sensor_trigger_locked(
                    f"debounce ativo ({trigger_time - self._last_ir_trigger:.3f}s < {self.debounce_time:.3f}s)",
                    channel,
                )
                return

            if channel is not None and self.sensor_require_rearm and not self._sensor_armed:
                self._ignore_sensor_trigger_locked(
                    "sensor aguardando rearme com feixe livre",
                    channel,
                )
                return

            if self._estado not in ("autorizado", "rodando"):
                self._ignore_sensor_trigger_locked(
                    f"estado {self._estado} nao aceita acionamento do sensor",
                    channel,
                )
                return

            self._last_ir_trigger = trigger_time
            if channel is not None and self.sensor_require_rearm:
                self._sensor_armed = False
                self._sensor_high_since = None

            if self._estado == "autorizado":
                self._t_inicio_prova = trigger_time
                self._hora_inicio_prova = datetime.now().isoformat()
                self._estado = "rodando"
                self._mark_state_changed_locked()
                self._record_sensor_event_locked("aceito_inicio", channel)
                logging.info(f"Prova iniciada. TIA: {self._get_tia():.3f}s")
            elif self._estado == "rodando":
                self._t_fim_prova = trigger_time
                self._hora_fim_prova = datetime.now().isoformat()
                self._estado = "finalizado"
                self._mark_state_changed_locked()
                self._record_sensor_event_locked("aceito_fim", channel)
                logging.info(f"Prova finalizada. TOP: {self._get_top():.3f}s")

    def _record_sensor_event_locked(self, event_type, channel, reason=None):
        event = {
            "tipo": event_type,
            "estado": self._estado,
            "canal": channel,
            "motivo": reason,
            "nivel": self._last_sensor_level,
            "estado_sinal": self._sensor_level_status(self._last_sensor_level),
            "armado": self._sensor_armed,
            "hora": datetime.now().isoformat(),
            "versao": self._state_version,
        }
        self._sensor_last_event = event
        if event_type.startswith("aceito"):
            self._sensor_accepted_count += 1
            self._sensor_last_accepted_event = event

    def _mark_state_changed_locked(self):
        self._state_version += 1
        self._updated_at = datetime.now().isoformat()
        for listener in tuple(self._state_change_listeners):
            try:
                listener(self._state_version)
            except Exception:
                logging.exception("Falha ao notificar alteracao de estado do cronometro.")

    def add_state_change_listener(self, listener):
        with self._lock:
            if listener not in self._state_change_listeners:
                self._state_change_listeners.append(listener)

    def _set_calibration_progress(self, update):
        with self._lock:
            self._calibration_status.update(
                {
                    "phase": update.get("phase", self._calibration_status.get("phase")),
                    "frequency_hz": update.get(
                        "frequency_hz",
                        self._calibration_status.get("frequency_hz"),
                    ),
                    "error": None,
                }
            )
            self._mark_state_changed_locked()

    def _mark_calibration_started(self, trigger):
        now = datetime.now().isoformat()
        with self._lock:
            self._calibration_running = True
            self._calibration_status.update(
                {
                    "running": True,
                    "phase": "starting",
                    "frequency_hz": None,
                    "started_at": now,
                    "finished_at": None,
                    "trigger": trigger,
                    "error": None,
                    "last_attempt": None,
                }
            )
            self._mark_state_changed_locked()

    def _mark_calibration_finished(self, result=None, error=None):
        now = datetime.now().isoformat()
        valid = calibration_result_is_valid(result)
        with self._lock:
            self._calibration_running = False
            self._calibration_status.update(
                {
                    "running": False,
                    "phase": "finished" if error is None else "error",
                    "frequency_hz": None,
                    "finished_at": now,
                    "error": error,
                }
            )
            if result is not None:
                self._calibration_status["last_attempt"] = result
                if valid:
                    self._calibration_last_result = result
                    self._calibration_status["last_result"] = result
            self._mark_state_changed_locked()

    def calibrate_ir_sensor(self, apply=True, save=True, trigger="manual"):
        if GPIO is None:
            raise CalibrationError(f"RPi.GPIO indisponivel: {GPIO_IMPORT_ERROR}")

        if not self._calibration_lock.acquire(blocking=False):
            raise RuntimeError("Calibracao IR ja esta em execucao")

        try:
            with self._lock:
                if self._estado in ("autorizado", "rodando"):
                    raise RuntimeError(
                        "Calibracao IR bloqueada durante prova autorizada ou em andamento"
                    )

            self._mark_calibration_started(trigger)
            logging.info("Iniciando calibracao IR (%s).", trigger)

            try:
                self._stop_sensor_input()
                self._stop_ir_emitter()
                result = run_ir_calibration(
                    GPIO,
                    sensor_pin=self.ir_pin,
                    emitter_pin=self.ir_led_pin,
                    duty=self.ir_duty_cycle,
                    duration=IR_CALIBRATION_DURATION_DEFAULT,
                    interval=self.sensor_poll_interval,
                    settle=IR_CALIBRATION_SETTLE_DEFAULT,
                    recovery=IR_CALIBRATION_RECOVERY_DEFAULT,
                    start=IR_CALIBRATION_START_DEFAULT,
                    stop=IR_CALIBRATION_STOP_DEFAULT,
                    step=IR_CALIBRATION_STEP_DEFAULT,
                    sensitivity_delta=IR_CALIBRATION_SENSITIVITY_DELTA_DEFAULT,
                    hold_duration=IR_CALIBRATION_HOLD_DEFAULT,
                    saturation_gap=IR_CALIBRATION_SATURATION_GAP_DEFAULT,
                    preferred_frequency=IR_CALIBRATION_PREFERRED_FREQUENCY_DEFAULT,
                    preference_tolerance=IR_CALIBRATION_PREFERENCE_TOLERANCE_DEFAULT,
                    pwm_backend=self.ir_pwm_backend,
                    pwm_chip=self.ir_pwm_chip,
                    pwm_channel=self.ir_pwm_channel,
                    progress=self._set_calibration_progress,
                    burst_on=self.ir_burst_on_time,
                    burst_off=self.ir_burst_off_time,
                    max_signal_timeout=self.sensor_signal_timeout,
                )
                recommendation = result.get("recommendation")
                valid = calibration_result_is_valid(result)
                if apply and valid:
                    self._apply_ir_recommendation(
                        recommendation,
                        override_env=True,
                        source=f"calibracao {trigger}",
                    )
                if save and valid:
                    self._save_ir_calibration_file(result)
                self._mark_calibration_finished(result=result)
                logging.info(
                    "Calibracao IR concluida. Recomendacao: %s",
                    recommendation,
                )
                return self.get_ir_config_status()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._mark_calibration_finished(error=error)
                logging.exception("Falha durante calibracao IR.")
                raise
            finally:
                self._restart_gpio_processing()
        finally:
            self._calibration_lock.release()

    def get_ir_config_status(self):
        with self._lock:
            return {
                "prova_estado": self._estado,
                "hardware": self.get_hardware_status(),
                "calibration": copy.deepcopy(self._calibration_status),
                "saved_calibration": copy.deepcopy(self._calibration_last_result),
                "electrical_warning": (
                    "Raspberry Pi GPIO aceita 3.3V. Se o circuito do receptor IR estiver "
                    "alimentado em 5V, confirme com multimetro/osciloscopio que o sinal no "
                    "GPIO17 nao passa de 3.3V ou use divisor/level shifter."
                ),
            }

    def _ignore_sensor_trigger_locked(self, reason, channel=None):
        self._sensor_ignored_count += 1
        self._sensor_last_ignored_reason = reason
        self._record_sensor_event_locked("ignorado", channel, reason)
        now = time.perf_counter()
        should_log = (
            now - self._sensor_last_ignore_log >= self.sensor_ignored_log_interval
            or self._sensor_ignored_count <= 3
        )
        if should_log:
            self._sensor_last_ignore_log = now
            logging.info(
                "Disparo do sensor ignorado: %s. Total ignorado: %s.",
                reason,
                self._sensor_ignored_count,
            )

    def prepare(self, id_inscricao, dados):
        with self._lock:
            self._estado = "preparado"
            self._id_inscricao = id_inscricao
            self._dados_inscricao = dados
            self._t_autorizado = None
            self._t_inicio_prova = None
            self._t_fim_prova = None
            self._hora_autorizacao = None
            self._hora_inicio_prova = None
            self._hora_fim_prova = None
            self._faltas = 0
            self._recusas = 0
            self._mark_state_changed_locked()
            logging.info(f"Preparado para inscrição #{id_inscricao}")

    def autorizar(self):
        with self._lock:
            self._last_authorize_error = None
            if self._estado != "preparado":
                self._last_authorize_error = "Estado inválido para autorizar"
                return False

            if self.sensor_require_ready and not self._sensor_ready_to_start_locked():
                self._last_authorize_error = (
                    "Feixe IR não alinhado. Ajuste emissor/receptor antes de autorizar a largada."
                )
                logging.warning(
                    "%s Nivel atual: %s (%s). Nivel esperado livre/alinhado: %s. "
                    "Amostra pronto: %s.",
                    self._last_authorize_error,
                    self._last_sensor_level,
                    self._sensor_level_status(self._last_sensor_level),
                    self._sensor_inactive_level(),
                    self._sensor_last_ready_check,
                )
                return False

            self._t_autorizado = time.perf_counter()
            self._hora_autorizacao = datetime.now().isoformat()
            self._estado = "autorizado"
            self._sensor_armed = True
            self._last_ir_trigger = 0
            self._mark_state_changed_locked()
            logging.info("Largada autorizada. TIA contando.")
            return True

    def get_last_authorize_error(self):
        return self._last_authorize_error

    def forcar_fim(self):
        with self._lock:
            if self._estado != "rodando":
                return False
            self._t_fim_prova = time.perf_counter()
            self._hora_fim_prova = datetime.now().isoformat()
            self._estado = "finalizado"
            self._mark_state_changed_locked()
            logging.info(f"Fim forçado. TOP: {self._get_top():.3f}s")
            return True

    def add_falta(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado"):
                self._faltas += 1
                self._mark_state_changed_locked()
                return True
            return False

    def remove_falta(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado") and self._faltas > 0:
                self._faltas -= 1
                self._mark_state_changed_locked()
                return True
            return False

    def add_recusa(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado"):
                self._recusas += 1
                self._mark_state_changed_locked()
                return True
            return False

    def remove_recusa(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado") and self._recusas > 0:
                self._recusas -= 1
                self._mark_state_changed_locked()
                return True
            return False

    def reset(self):
        with self._lock:
            old_id = self._id_inscricao
            self._estado = "idle"
            self._id_inscricao = None
            self._dados_inscricao = {}
            self._t_autorizado = None
            self._t_inicio_prova = None
            self._t_fim_prova = None
            self._hora_autorizacao = None
            self._hora_inicio_prova = None
            self._hora_fim_prova = None
            self._faltas = 0
            self._recusas = 0
            self._mark_state_changed_locked()
            logging.info("Cronômetro resetado.")
            return old_id

    def simular_acionamento(self):
        """Simula acionamento do sensor/botão (para dev/testes sem GPIO)."""
        self._ir_callback(None)

    def _get_tia(self):
        if self._t_autorizado is None:
            return 0.0
        if self._t_inicio_prova is not None:
            return self._t_inicio_prova - self._t_autorizado
        if self._estado == "autorizado":
            return time.perf_counter() - self._t_autorizado
        return 0.0

    def _get_top(self):
        if self._t_inicio_prova is None:
            return 0.0
        if self._t_fim_prova is not None:
            return self._t_fim_prova - self._t_inicio_prova
        if self._estado == "rodando":
            return time.perf_counter() - self._t_inicio_prova
        return 0.0

    @staticmethod
    def _format_time(t):
        minutes = int(t // 60)
        seconds = int(t % 60)
        millis = int((t - int(t)) * 1000)
        return f"{minutes:02d}:{seconds:02d}.{millis:03d}"

    def get_estado_completo(self):
        with self._lock:
            tia = self._get_tia()
            top = self._get_top()

            result = {
                "estado": self._estado,
                "id_inscricao": self._id_inscricao,
                "versao": self._state_version,
                "atualizado_em": self._updated_at,
                "tia_decorrido": round(tia, 3),
                "tia_str": self._format_time(tia),
                "top_decorrido": round(top, 3),
                "top_str": self._format_time(top),
                "tempo_oficial": round(top, 3) if self._estado == "finalizado" else None,
                "faltas": self._faltas,
                "recusas": self._recusas,
            }
            result.update(self._dados_inscricao)
            return result

    def get_hardware_status(self):
        return {
            "raspberry_pi": IS_RASPBERRY_PI,
            "raspberry_modelo": RASPBERRY_PI_MODEL,
            "python_executavel": sys.executable,
            "python_prefixo": sys.prefix,
            "gpio_disponivel": GPIO is not None,
            "gpio_import_origem": GPIO_IMPORT_SOURCE,
            "gpio_import_erro": GPIO_IMPORT_ERROR,
            "gpio_pronto": self._gpio_ready,
            "gpio_erro": self._gpio_error,
            "sensor_gpio_bcm": self.ir_pin,
            "sensor_modo": self._sensor_mode,
            "sensor_erro": self._sensor_error,
            "sensor_fallback_motivo": self._sensor_fallback_reason,
            "sensor_read_mode": self.sensor_read_mode,
            "sensor_active_level": self.sensor_active_level,
            "sensor_nivel_atual": self._last_sensor_level,
            "sensor_estado_sinal": self._sensor_level_status(self._last_sensor_level),
            "sensor_estado_feixe": self._logical_beam_status_locked(),
            "sensor_feixe_logico_alinhado": self._beam_aligned,
            "sensor_feixe_alinhado": self._beam_aligned,
            "sensor_feixe_quebrado": not self._beam_aligned,
            "sensor_raw_feixe_alinhado": self._last_sensor_level == self._sensor_inactive_level(),
            "sensor_raw_feixe_quebrado": self._last_sensor_level == self._sensor_active_level(),
            "sensor_nivel_ativo": self._sensor_active_level(),
            "sensor_nivel_inativo": self._sensor_inactive_level(),
            "sensor_poll_interval": self.sensor_poll_interval,
            "sensor_debounce": self.debounce_time,
            "sensor_rearm_stable": self.sensor_rearm_stable_time,
            "sensor_trigger_confirm": self.sensor_trigger_confirm_time,
            "sensor_signal_timeout": self.sensor_signal_timeout,
            "sensor_ready_confirm": self.sensor_ready_confirm_time,
            "sensor_ready_min_ratio": self.sensor_ready_min_ratio,
            "sensor_ultima_amostra_pronto": self._sensor_last_ready_check,
            "sensor_require_rearm": self.sensor_require_rearm,
            "sensor_require_ready": self.sensor_require_ready,
            "sensor_armado": self._sensor_armed,
            "sensor_disparos_aceitos": self._sensor_accepted_count,
            "sensor_disparos_ignorados": self._sensor_ignored_count,
            "sensor_ultimo_ignorado": self._sensor_last_ignored_reason,
            "sensor_ultimo_evento": self._sensor_last_event,
            "sensor_ultimo_aceito": self._sensor_last_accepted_event,
            "sensor_transicoes": self._sensor_transition_count,
            "sensor_ultima_transicao": self._sensor_last_transition,
            "sensor_ultimo_sinal_atras_s": (
                round(time.perf_counter() - self._beam_last_signal_at, 3)
                if self._beam_last_signal_at is not None
                else None
            ),
            "sensor_quebras_logicas": self._beam_break_count,
            "sensor_ignored_log_interval": self.sensor_ignored_log_interval,
            "pigpio_disponivel": pigpio is not None,
            "pigpio_import_origem": PIGPIO_IMPORT_SOURCE,
            "pigpio_import_erro": PIGPIO_IMPORT_ERROR,
            "pigpio_conectado": self._pigpio.connected if self._pigpio is not None else False,
            "emissor_habilitado": self.ir_emitter_enabled,
            "emissor_gpio_bcm": self.ir_led_pin,
            "emissor_ativo": self._ir_emitter_active,
            "emissor_modo": self._ir_emitter_mode,
            "emissor_erro": self._ir_emitter_error,
            "emissor_pwm_backend": self.ir_pwm_backend,
            "emissor_pwm_chip": self.ir_pwm_chip,
            "emissor_pwm_channel": kernel_pwm_channel_for_pin(
                self.ir_led_pin,
                self.ir_pwm_channel,
            )
            if self.ir_led_pin in HARDWARE_PWM_PINS
            else self.ir_pwm_channel,
            "emissor_kernel_pwm_ativo": self._using_kernel_pwm,
            "emissor_kernel_pwm_path": (
                str(self._kernel_pwm.pwm_path) if self._kernel_pwm is not None else None
            ),
            "emissor_hardware_pwm_gpio_suportados": sorted(HARDWARE_PWM_PINS),
            "emissor_rajada_habilitada": self.ir_burst_enabled,
            "emissor_rajada_on": self.ir_burst_on_time,
            "emissor_rajada_off": self.ir_burst_off_time,
            "emissor_rajada_periodo": self.ir_burst_on_time + self.ir_burst_off_time,
            "emissor_thread_ativa": (
                self._ir_burst_thread.is_alive()
                if self._ir_burst_thread is not None
                else False
            ),
            "frequencia_hz": self.ir_frequency,
            "duty_cycle": self.ir_duty_cycle,
            "calibracao_em_execucao": self._calibration_running,
            "calibracao_status": copy.deepcopy(self._calibration_status),
        }

    def get_dados_confirmacao(self):
        with self._lock:
            if self._estado != "finalizado":
                return None
            return {
                "id_inscricao": self._id_inscricao,
                "tia": round(self._get_tia(), 3),
                "top": round(self._get_top(), 3),
                "faltas": self._faltas,
                "recusas": self._recusas,
                "hora_autorizacao": self._hora_autorizacao,
                "hora_inicio_prova": self._hora_inicio_prova,
                "hora_fim_prova": self._hora_fim_prova,
            }

    def cleanup(self):
        self._stop_sensor_input()
        self._stop_ir_emitter()

        if GPIO:
            pins = list({pin for pin in (self.ir_pin, self.ir_led_pin) if pin})
            if pins:
                GPIO.cleanup(pins)
