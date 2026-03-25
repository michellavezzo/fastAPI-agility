import time

# ...existing code...
import threading
import logging
import requests
try:
	import RPi.GPIO as GPIO
except ImportError:
	GPIO = None  # Permite testes fora da Raspberry Pi

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

class Chronometer:
	def __init__(self, ir_pin=None, backend_url=None, debounce_time=0.1):
		self.ir_pin = ir_pin
		self.backend_url = backend_url
		self.debounce_time = debounce_time
		self._start_time = None
		self._stop_time = None
		self._prepared = False
		self._running = False
		self._lock = threading.Lock()
		self._last_ir_trigger = 0
		if GPIO and self.ir_pin:
			GPIO.setmode(GPIO.BCM)
			GPIO.setup(self.ir_pin, GPIO.IN)
			GPIO.add_event_detect(self.ir_pin, GPIO.FALLING, callback=self._ir_callback, bouncetime=int(self.debounce_time*1000))

	def prepare(self):
		"""Preparação da prova, chamada pelo backend."""
		with self._lock:
			self._prepared = True
			self._start_time = None
			self._stop_time = None
			self._running = False
			logging.info("Cronômetro preparado para início da prova.")

	def _ir_callback(self, channel):
		now = time.perf_counter()
		with self._lock:
			# Debounce manual extra
			if now - self._last_ir_trigger < self.debounce_time:
				return
			self._last_ir_trigger = now
			if self._prepared and not self._running:
				self.start()
			elif self._running:
				self.stop()

	def start(self):
		with self._lock:
			if not self._prepared:
				logging.warning("Tentativa de iniciar sem preparação.")
				return
			if self._running:
				logging.warning("Cronômetro já está rodando.")
				return
			self._start_time = time.perf_counter()
			self._running = True
			logging.info("Cronômetro iniciado.")
			self._notify_backend('start')

	def stop(self):
		with self._lock:
			if not self._running:
				logging.warning("Tentativa de parar sem estar rodando.")
				return
			self._stop_time = time.perf_counter()
			self._running = False
			logging.info(f"Cronômetro parado. Tempo: {self.get_time_str()}")
			self._notify_backend('stop', self.get_time())

	def reset(self):
		with self._lock:
			self._start_time = None
			self._stop_time = None
			self._prepared = False
			self._running = False
			logging.info("Cronômetro resetado.")

	def get_time(self):
		with self._lock:
			if self._start_time is None:
				return 0.0
			end = self._stop_time if self._stop_time else time.perf_counter() if self._running else self._start_time
			return max(0.0, end - self._start_time)

	def get_time_str(self):
		t = self.get_time()
		minutes = int(t // 60)
		seconds = int(t % 60)
		millis = int((t - int(t)) * 1000)
		return f"{minutes:02d}:{seconds:02d}:{millis:03d}"

	def _notify_backend(self, event, time_value=None):
		if not self.backend_url:
			return
		payload = {'event': event}
		if time_value is not None:
			payload['time'] = time_value
		try:
			requests.post(self.backend_url, json=payload, timeout=2)
			logging.info(f"Notificado backend: {payload}")
		except Exception as e:
			logging.error(f"Falha ao notificar backend: {e}")

	def cleanup(self):
		if GPIO and self.ir_pin:
			GPIO.cleanup(self.ir_pin)

# Exemplo de uso (substitua pelos endpoints reais e pin do sensor)
if __name__ == "__main__":
	chrono = Chronometer(ir_pin=17, backend_url="http://localhost:8000/api/cronometro")
	try:
		while True:
			time.sleep(0.1)
	except KeyboardInterrupt:
		chrono.cleanup()
