import time
import os
import threading
import logging
from datetime import datetime

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

GPIO_PIN_DEFAULT = int(os.environ.get("AGILITY_GPIO_PIN", "17"))


class Chronometer:
    """Cronômetro de prova de Agility com dois tempos:
    - TIA (Tempo de Início Autorizado): da autorização ao 1o acionamento do sensor/botão
    - TOP (Tempo Oficial da Prova): do 1o ao 2o acionamento do sensor/botão
    """

    def __init__(self, ir_pin=None, debounce_time=0.3):
        self.ir_pin = ir_pin if ir_pin is not None else GPIO_PIN_DEFAULT
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
        if GPIO and self.ir_pin:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.ir_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self.ir_pin, GPIO.FALLING,
                callback=self._ir_callback,
                bouncetime=int(self.debounce_time * 1000)
            )
            logging.info(f"GPIO {self.ir_pin} configurado com pull-up interno.")

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
        if GPIO and self.ir_pin:
            GPIO.cleanup(self.ir_pin)
