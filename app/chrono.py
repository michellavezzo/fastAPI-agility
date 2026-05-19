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
IR_FREQUENCY_DEFAULT = int(os.environ.get("AGILITY_IR_FREQUENCY", "38000"))
IR_DUTY_CYCLE_DEFAULT = float(os.environ.get("AGILITY_IR_DUTY_CYCLE", "50"))
SENSOR_POLL_INTERVAL_DEFAULT = float(os.environ.get("AGILITY_SENSOR_POLL_INTERVAL", "0.005"))


def _env_bool(name, default=True):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


IR_EMITTER_ENABLED_DEFAULT = _env_bool("AGILITY_IR_EMITTER_ENABLED", True)


class Chronometer:
    """Cronômetro de prova de Agility com dois tempos:
    - TIA (Tempo de Início Autorizado): da autorização ao 1o acionamento do sensor/botão
    - TOP (Tempo Oficial da Prova): do 1o ao 2o acionamento do sensor/botão
    """

    def __init__(
        self,
        ir_pin=None,
        debounce_time=0.3,
        ir_led_pin=None,
        ir_frequency=IR_FREQUENCY_DEFAULT,
        ir_duty_cycle=IR_DUTY_CYCLE_DEFAULT,
        ir_emitter_enabled=IR_EMITTER_ENABLED_DEFAULT,
        sensor_poll_interval=SENSOR_POLL_INTERVAL_DEFAULT,
    ):
        self.ir_pin = ir_pin if ir_pin is not None else GPIO_PIN_DEFAULT
        self.ir_led_pin = ir_led_pin if ir_led_pin is not None else IR_LED_PIN_DEFAULT
        self.ir_frequency = ir_frequency
        self.ir_duty_cycle = ir_duty_cycle
        self.ir_emitter_enabled = ir_emitter_enabled
        self.sensor_poll_interval = max(0.001, float(sensor_poll_interval))
        self._ir_pwm = None
        self._pigpio = None
        self._using_pigpio_pwm = False
        self._gpio_ready = False
        self._ir_emitter_active = False
        self._ir_emitter_mode = None
        self._ir_emitter_error = None
        self._gpio_error = None
        self._sensor_mode = None
        self._sensor_error = None
        self._sensor_poll_stop = threading.Event()
        self._sensor_poll_thread = None
        self._last_sensor_level = None
        self.debounce_time = debounce_time
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
        logging.info(
            "Sensor IR configurado no GPIO %s com pull-up interno. Nivel inicial: %s.",
            self.ir_pin,
            self._last_sensor_level,
        )

        try:
            GPIO.add_event_detect(
                self.ir_pin,
                GPIO.FALLING,
                callback=self._ir_callback,
                bouncetime=int(self.debounce_time * 1000),
            )
            self._sensor_mode = "event_detect"
            self._sensor_error = None
            self._gpio_ready = True
            self._gpio_error = None
            logging.info(
                "Sensor IR usando interrupcao RPi.GPIO.add_event_detect no GPIO %s.",
                self.ir_pin,
            )
        except Exception as exc:
            self._sensor_error = f"{type(exc).__name__}: {exc}"
            logging.exception(
                "Falha ao registrar interrupcao no GPIO %s. "
                "Possiveis causas: outro processo usando o pino, backend duplicado, "
                "permissao de GPIO ou limitacao da versao do RPi.GPIO/kernel. "
                "Ativando fallback por polling.",
                self.ir_pin,
            )
            self._start_sensor_polling()

    def _start_sensor_polling(self):
        if self._sensor_poll_thread and self._sensor_poll_thread.is_alive():
            return

        self._sensor_poll_stop.clear()
        self._sensor_mode = "polling"
        self._gpio_ready = True
        self._gpio_error = None
        self._sensor_poll_thread = threading.Thread(
            target=self._sensor_poll_loop,
            name="agility-gpio17-polling",
            daemon=True,
        )
        self._sensor_poll_thread.start()
        logging.warning(
            "Sensor IR usando polling no GPIO %s a cada %.3fs. "
            "O cronometro continua funcional, mas a precisao depende desse intervalo.",
            self.ir_pin,
            self.sensor_poll_interval,
        )

    def _sensor_poll_loop(self):
        while not self._sensor_poll_stop.is_set():
            try:
                current_level = GPIO.input(self.ir_pin)
                previous_level = self._last_sensor_level
                self._last_sensor_level = current_level

                if previous_level == GPIO.HIGH and current_level == GPIO.LOW:
                    self._ir_callback(self.ir_pin)
            except Exception as exc:
                self._gpio_ready = False
                self._gpio_error = f"{type(exc).__name__}: {exc}"
                self._sensor_error = self._gpio_error
                logging.exception("Falha ao ler GPIO %s em modo polling.", self.ir_pin)
                return

            self._sensor_poll_stop.wait(self.sensor_poll_interval)

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
                        self.ir_frequency,
                        int(duty_cycle * 10000),
                    )
                    self._using_pigpio_pwm = True
                    self._ir_emitter_active = True
                    self._ir_emitter_mode = "pigpio.hardware_PWM"
                    self._ir_emitter_error = None
                    logging.info(
                        f"LED IR em GPIO {self.ir_led_pin} com PWM hardware "
                        f"{self.ir_frequency}Hz e duty {duty_cycle}%."
                    )
                    return

                self._pigpio = None
                logging.warning("pigpio importado, mas daemon pigpiod nao conectado. Usando PWM via RPi.GPIO.")
            except Exception as exc:
                self._pigpio = None
                logging.warning("Falha ao iniciar PWM via pigpio: %s. Usando PWM via RPi.GPIO.", exc)

        try:
            GPIO.setup(self.ir_led_pin, GPIO.OUT, initial=GPIO.LOW)
            self._ir_pwm = GPIO.PWM(self.ir_led_pin, self.ir_frequency)
            self._ir_pwm.start(duty_cycle)
            self._ir_emitter_active = True
            self._ir_emitter_mode = "RPi.GPIO.PWM"
            self._ir_emitter_error = None
            logging.info(
                f"LED IR em GPIO {self.ir_led_pin} com PWM {self.ir_frequency}Hz "
                f"e duty {duty_cycle}%."
            )
        except Exception as exc:
            self._ir_emitter_active = False
            self._ir_emitter_error = f"{type(exc).__name__}: {exc}"
            logging.exception("Falha ao inicializar emissor IR.")

    def _ir_callback(self, channel):
        now = time.perf_counter()
        with self._lock:
            if now - self._last_ir_trigger < self.debounce_time:
                return
            self._last_ir_trigger = now

            if self._estado == "autorizado":
                self._t_inicio_prova = now
                self._hora_inicio_prova = datetime.now().isoformat()
                self._estado = "rodando"
                logging.info(f"Prova iniciada. TIA: {self._get_tia():.3f}s")
            elif self._estado == "rodando":
                self._t_fim_prova = now
                self._hora_fim_prova = datetime.now().isoformat()
                self._estado = "finalizado"
                logging.info(f"Prova finalizada. TOP: {self._get_top():.3f}s")

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
            logging.info(f"Preparado para inscrição #{id_inscricao}")

    def autorizar(self):
        with self._lock:
            if self._estado != "preparado":
                return False
            self._t_autorizado = time.perf_counter()
            self._hora_autorizacao = datetime.now().isoformat()
            self._estado = "autorizado"
            logging.info("Largada autorizada. TIA contando.")
            return True

    def forcar_fim(self):
        with self._lock:
            if self._estado != "rodando":
                return False
            self._t_fim_prova = time.perf_counter()
            self._hora_fim_prova = datetime.now().isoformat()
            self._estado = "finalizado"
            logging.info(f"Fim forçado. TOP: {self._get_top():.3f}s")
            return True

    def add_falta(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado"):
                self._faltas += 1
                return True
            return False

    def remove_falta(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado") and self._faltas > 0:
                self._faltas -= 1
                return True
            return False

    def add_recusa(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado"):
                self._recusas += 1
                return True
            return False

    def remove_recusa(self):
        with self._lock:
            if self._estado in ("rodando", "finalizado") and self._recusas > 0:
                self._recusas -= 1
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
            "sensor_poll_interval": self.sensor_poll_interval,
            "pigpio_disponivel": pigpio is not None,
            "pigpio_import_origem": PIGPIO_IMPORT_SOURCE,
            "pigpio_import_erro": PIGPIO_IMPORT_ERROR,
            "emissor_habilitado": self.ir_emitter_enabled,
            "emissor_gpio_bcm": self.ir_led_pin,
            "emissor_ativo": self._ir_emitter_active,
            "emissor_modo": self._ir_emitter_mode,
            "emissor_erro": self._ir_emitter_error,
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

        if self._using_pigpio_pwm and self._pigpio is not None:
            self._pigpio.hardware_PWM(self.ir_led_pin, 0, 0)
            self._pigpio.stop()

        if self._ir_pwm is not None:
            self._ir_pwm.stop()

        if GPIO:
            pins = list({pin for pin in (self.ir_pin, self.ir_led_pin) if pin})
            if pins:
                GPIO.cleanup(pins)
