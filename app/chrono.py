import time
import os
import threading
import logging
import importlib
import platform
import sys
from datetime import datetime
from pathlib import Path


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
            "O pigpio e opcional; se nao existir no APT desta imagem, o emissor IR usa RPi.GPIO.PWM."
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
IR_FREQUENCY_DEFAULT = int(os.environ.get("AGILITY_IR_FREQUENCY", "31000"))
IR_DUTY_CYCLE_DEFAULT = float(os.environ.get("AGILITY_IR_DUTY_CYCLE", "50"))
IR_BURST_ON_DEFAULT = float(os.environ.get("AGILITY_IR_BURST_ON", "0.002"))
IR_BURST_OFF_DEFAULT = float(os.environ.get("AGILITY_IR_BURST_OFF", "0.002"))
SENSOR_DEBOUNCE_DEFAULT = float(os.environ.get("AGILITY_SENSOR_DEBOUNCE", "1.0"))
SENSOR_REARM_STABLE_DEFAULT = float(os.environ.get("AGILITY_SENSOR_REARM_STABLE", "0.02"))
SENSOR_POLL_INTERVAL_DEFAULT = float(os.environ.get("AGILITY_SENSOR_POLL_INTERVAL", "0.001"))
SENSOR_ACTIVE_LEVEL_DEFAULT = os.environ.get("AGILITY_SENSOR_ACTIVE_LEVEL", "LOW").strip().upper()
SENSOR_READ_MODE_DEFAULT = os.environ.get("AGILITY_SENSOR_READ_MODE", "auto").strip().lower()
SENSOR_TRIGGER_CONFIRM_DEFAULT = float(os.environ.get("AGILITY_SENSOR_TRIGGER_CONFIRM", "0.002"))
SENSOR_SIGNAL_TIMEOUT_DEFAULT = float(os.environ.get("AGILITY_SENSOR_SIGNAL_TIMEOUT", "0.03"))
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
        self._using_pigpio_pwm = False
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
        self.debounce_time = max(0.001, float(debounce_time))
        self._lock = threading.RLock()
        self._last_ir_trigger = 0

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

                if should_trigger:
                    self._ir_callback(self.ir_pin)
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

        if current_level == self._sensor_inactive_level():
            self._beam_last_signal_at = now
            self._beam_aligned = True
        elif self._beam_last_signal_at is None:
            self._beam_aligned = False
        elif now - self._beam_last_signal_at >= self.sensor_signal_timeout:
            self._beam_aligned = False

        if was_aligned and not self._beam_aligned:
            self._beam_last_break_at = now
            self._beam_break_count += 1
            return True

        return False

    def _sensor_ready_to_start_locked(self, now=None):
        if not self._gpio_ready or self._last_sensor_level is None:
            self._sensor_last_ready_check = None
            return True

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

        if pigpio is not None:
            try:
                self._pigpio = pigpio.pi()
                if self._pigpio.connected:
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
                logging.warning("pigpio importado, mas daemon pigpiod nao conectado. Usando PWM via RPi.GPIO.")
            except Exception as exc:
                self._pigpio = None
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

    def _ir_callback(self, channel):
        now = time.perf_counter()
        confirmed_level = None
        if channel is not None and self.sensor_trigger_confirm_time > 0 and GPIO is not None:
            time.sleep(self.sensor_trigger_confirm_time)
            confirmed_level = GPIO.input(self.ir_pin)
            now = time.perf_counter()

        with self._lock:
            if confirmed_level is not None:
                self._refresh_sensor_level_locked(confirmed_level, now)
                if confirmed_level != self._sensor_active_level():
                    self._ignore_sensor_trigger_locked(
                        "pulso descartado: nivel ativo nao permaneceu estavel",
                        channel,
                    )
                    return

            if now - self._last_ir_trigger < self.debounce_time:
                self._ignore_sensor_trigger_locked(
                    f"debounce ativo ({now - self._last_ir_trigger:.3f}s < {self.debounce_time:.3f}s)",
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

            self._last_ir_trigger = now
            if channel is not None and self.sensor_require_rearm:
                self._sensor_armed = False
                self._sensor_high_since = None

            if self._estado == "autorizado":
                self._t_inicio_prova = now
                self._hora_inicio_prova = datetime.now().isoformat()
                self._estado = "rodando"
                self._mark_state_changed_locked()
                self._record_sensor_event_locked("aceito_inicio", channel)
                logging.info(f"Prova iniciada. TIA: {self._get_tia():.3f}s")
            elif self._estado == "rodando":
                self._t_fim_prova = now
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
            "emissor_habilitado": self.ir_emitter_enabled,
            "emissor_gpio_bcm": self.ir_led_pin,
            "emissor_ativo": self._ir_emitter_active,
            "emissor_modo": self._ir_emitter_mode,
            "emissor_erro": self._ir_emitter_error,
            "emissor_rajada_habilitada": self.ir_burst_enabled,
            "emissor_rajada_on": self.ir_burst_on_time,
            "emissor_rajada_off": self.ir_burst_off_time,
            "frequencia_hz": self.ir_frequency,
            "duty_cycle": self.ir_duty_cycle,
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
        if self._sensor_poll_thread and self._sensor_poll_thread.is_alive():
            self._sensor_poll_stop.set()
            self._sensor_poll_thread.join(timeout=1)

        if self._ir_burst_thread and self._ir_burst_thread.is_alive():
            self._ir_burst_stop.set()
            self._ir_burst_thread.join(timeout=1)

        if self._using_pigpio_pwm and self._pigpio is not None:
            self._pigpio.hardware_PWM(self.ir_led_pin, 0, 0)
            self._pigpio.stop()

        if self._ir_pwm is not None:
            self._ir_pwm.stop()

        if GPIO:
            pins = list({pin for pin in (self.ir_pin, self.ir_led_pin) if pin})
            if pins:
                GPIO.cleanup(pins)
