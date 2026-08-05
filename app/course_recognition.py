import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable


MIN_RECOGNITION_SECONDS = 420
PRE_START_INTERVAL_SECONDS = 180


class CourseRecognitionTimer:
    def __init__(self, clock=time.perf_counter, wall_clock=None):
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._listeners = []
        self.reset()

    def start(
        self,
        id_prova: int,
        session_id: int,
        duration_seconds: int,
        started_at: datetime | None = None,
    ) -> dict:
        if duration_seconds < MIN_RECOGNITION_SECONDS:
            raise ValueError(f"duration_seconds must be at least {MIN_RECOGNITION_SECONDS}")

        with self._lock:
            started_at = started_at or self._wall_clock()
            self._started_at = started_at
            self._phase_started_at = self._clock()
            self._state = {
                "estado": "reconhecimento",
                "id_prova": id_prova,
                "id_reconhecimento": session_id,
                "duracao_segundos": duration_seconds,
                "intervalo_segundos": PRE_START_INTERVAL_SECONDS,
                "reconhecimento_restante": float(duration_seconds),
                "intervalo_restante": None,
                "versao": self._state["versao"] + 1,
                "iniciado_em": self._format_timestamp(started_at),
                "reconhecimento_finalizado_em": None,
                "liberado_em": None,
                "cancelado_em": None,
            }
            state, listeners = self._state_and_listeners_locked()

        self._notify_listeners(listeners, state)
        return state

    def restore(
        self,
        id_prova: int,
        session_id: int,
        duration_seconds: int,
        interval_seconds: int,
        started_at: datetime,
        cancelled_at: datetime | None = None,
    ) -> dict:
        if duration_seconds < MIN_RECOGNITION_SECONDS:
            raise ValueError(f"duration_seconds must be at least {MIN_RECOGNITION_SECONDS}")
        if interval_seconds != PRE_START_INTERVAL_SECONDS:
            raise ValueError(f"interval_seconds must equal {PRE_START_INTERVAL_SECONDS}")

        with self._lock:
            version = self._state["versao"] + 1
            if cancelled_at is not None:
                self._state = self._waiting_state(version, cancelled_at)
            else:
                now = self._wall_clock()
                elapsed = max(0.0, (now - started_at).total_seconds())
                recognition_finished_at = started_at + timedelta(seconds=duration_seconds)
                released_at = recognition_finished_at + timedelta(seconds=interval_seconds)
                common_state = {
                    "id_prova": id_prova,
                    "id_reconhecimento": session_id,
                    "duracao_segundos": duration_seconds,
                    "intervalo_segundos": interval_seconds,
                    "versao": version,
                    "iniciado_em": self._format_timestamp(started_at),
                    "reconhecimento_finalizado_em": None,
                    "liberado_em": None,
                    "cancelado_em": None,
                }
                if elapsed < duration_seconds:
                    self._state = {
                        **common_state,
                        "estado": "reconhecimento",
                        "reconhecimento_restante": duration_seconds - elapsed,
                        "intervalo_restante": None,
                    }
                elif elapsed < duration_seconds + interval_seconds:
                    self._state = {
                        **common_state,
                        "estado": "intervalo",
                        "reconhecimento_restante": 0.0,
                        "intervalo_restante": duration_seconds + interval_seconds - elapsed,
                        "reconhecimento_finalizado_em": self._format_timestamp(recognition_finished_at),
                    }
                else:
                    self._state = {
                        **common_state,
                        "estado": "liberado",
                        "reconhecimento_restante": 0.0,
                        "intervalo_restante": 0.0,
                        "reconhecimento_finalizado_em": self._format_timestamp(recognition_finished_at),
                        "liberado_em": self._format_timestamp(released_at),
                    }
                self._started_at = started_at
                phase_elapsed = elapsed
                if self._state["estado"] == "intervalo":
                    phase_elapsed -= duration_seconds
                self._phase_started_at = self._clock() - max(0.0, phase_elapsed)
            state, listeners = self._state_and_listeners_locked()

        self._notify_listeners(listeners, state)
        return state

    def tick(self) -> dict:
        notifications = []
        with self._lock:
            now = self._clock()
            while self._state["estado"] in ("reconhecimento", "intervalo"):
                elapsed = max(0.0, now - self._phase_started_at)
                if self._state["estado"] == "reconhecimento":
                    duration = self._state["duracao_segundos"]
                    if elapsed < duration:
                        self._state["reconhecimento_restante"] = duration - elapsed
                        break

                    self._phase_started_at += duration
                    finished_at = self._started_at + timedelta(seconds=duration)
                    self._state.update(
                        {
                            "estado": "intervalo",
                            "reconhecimento_restante": 0.0,
                            "intervalo_restante": float(self._state["intervalo_segundos"]),
                            "versao": self._state["versao"] + 1,
                            "reconhecimento_finalizado_em": self._format_timestamp(finished_at),
                        }
                    )
                    notifications.append(self._state_and_listeners_locked())
                    continue

                interval = self._state["intervalo_segundos"]
                if elapsed < interval:
                    self._state["intervalo_restante"] = interval - elapsed
                    break

                released_at = self._started_at + timedelta(
                    seconds=self._state["duracao_segundos"] + interval
                )
                self._state.update(
                    {
                        "estado": "liberado",
                        "intervalo_restante": 0.0,
                        "versao": self._state["versao"] + 1,
                        "liberado_em": self._format_timestamp(released_at),
                    }
                )
                notifications.append(self._state_and_listeners_locked())

            state = dict(self._state)

        for notification, listeners in notifications:
            self._notify_listeners(listeners, notification)
        return state

    def cancel(self) -> dict:
        with self._lock:
            if self._state["estado"] == "aguardando":
                return dict(self._state)

            self._state = self._waiting_state(
                self._state["versao"] + 1,
                self._wall_clock(),
            )
            state, listeners = self._state_and_listeners_locked()

        self._notify_listeners(listeners, state)
        return state

    def is_released_for(self, id_prova: int) -> bool:
        with self._lock:
            return self._state["estado"] == "liberado" and self._state["id_prova"] == id_prova

    def add_state_change_listener(self, listener: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def reset(self) -> dict:
        with self._lock:
            version = self._state["versao"] + 1 if hasattr(self, "_state") else 0
            self._state = self._waiting_state(version)
            state, listeners = self._state_and_listeners_locked()

        self._notify_listeners(listeners, state)
        return state

    @staticmethod
    def _format_timestamp(value):
        return value.isoformat() if value is not None else None

    def _waiting_state(self, version, cancelled_at=None):
        return {
            "estado": "aguardando",
            "id_prova": None,
            "id_reconhecimento": None,
            "duracao_segundos": None,
            "intervalo_segundos": None,
            "reconhecimento_restante": None,
            "intervalo_restante": None,
            "versao": version,
            "iniciado_em": None,
            "reconhecimento_finalizado_em": None,
            "liberado_em": None,
            "cancelado_em": self._format_timestamp(cancelled_at),
        }

    def _state_and_listeners_locked(self):
        return dict(self._state), list(self._listeners)

    @staticmethod
    def _notify_listeners(listeners, state):
        for listener in listeners:
            try:
                listener(dict(state))
            except Exception:
                logging.exception("Falha ao notificar alteração do reconhecimento de pista.")
