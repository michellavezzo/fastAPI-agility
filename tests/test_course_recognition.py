from datetime import datetime, timezone

import pytest

from app.course_recognition import CourseRecognitionTimer


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_rejects_recognition_shorter_than_seven_minutes():
    timer = CourseRecognitionTimer(clock=FakeClock())

    with pytest.raises(ValueError, match="420"):
        timer.start(id_prova=7, session_id=3, duration_seconds=419)


def test_initial_state_is_waiting():
    state = CourseRecognitionTimer(clock=FakeClock()).get_state()

    assert state["estado"] == "aguardando"
    assert state["id_prova"] is None


def test_transitions_from_recognition_to_interval_and_release():
    clock = FakeClock()
    timer = CourseRecognitionTimer(clock=clock)
    timer.start(id_prova=7, session_id=3, duration_seconds=420)

    clock.advance(419.999)
    assert timer.tick()["estado"] == "reconhecimento"

    clock.advance(0.001)
    assert timer.tick()["estado"] == "intervalo"
    assert timer.get_state()["intervalo_restante"] == pytest.approx(180)

    clock.advance(180)
    assert timer.tick()["estado"] == "liberado"
    assert timer.is_released_for(7) is True
    assert timer.is_released_for(8) is False


def test_start_and_transitions_increment_version_and_notify_listener_once():
    clock = FakeClock()
    started_at = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    timer = CourseRecognitionTimer(clock=clock)
    changes = []
    timer.add_state_change_listener(changes.append)

    started = timer.start(7, 3, 420, started_at=started_at)

    assert started["id_reconhecimento"] == 3
    assert started["reconhecimento_restante"] == pytest.approx(420)
    assert started["versao"] == 1
    assert started["iniciado_em"] == "2026-08-04T12:00:00+00:00"
    assert changes == [started]

    assert timer.tick()["versao"] == 1
    assert len(changes) == 1

    clock.advance(420)
    interval = timer.tick()

    assert interval["versao"] == 2
    assert interval["reconhecimento_finalizado_em"] == "2026-08-04T12:07:00+00:00"
    assert len(changes) == 2


def test_cancel_requires_full_restart():
    clock = FakeClock()
    timer = CourseRecognitionTimer(clock=clock)
    timer.start(7, 3, 420)
    clock.advance(200)

    cancelled = timer.cancel()

    assert cancelled["estado"] == "aguardando"
    assert cancelled["id_prova"] is None
    assert cancelled["cancelado_em"] is not None
    assert timer.is_released_for(7) is False

    restarted = timer.start(8, 4, 420)
    assert restarted["estado"] == "reconhecimento"
    assert restarted["id_prova"] == 8


def test_restore_recomputes_interval_from_wall_time():
    wall_now = datetime(2026, 8, 4, 12, 8, tzinfo=timezone.utc)
    timer = CourseRecognitionTimer(
        clock=FakeClock(),
        wall_clock=lambda: wall_now,
    )
    timer.restore(
        id_prova=7,
        session_id=3,
        duration_seconds=420,
        interval_seconds=180,
        started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        cancelled_at=None,
    )

    assert timer.get_state()["estado"] == "intervalo"
    assert timer.get_state()["intervalo_restante"] == pytest.approx(120)


def test_restore_rejects_interval_different_from_three_minutes():
    timer = CourseRecognitionTimer(clock=FakeClock())

    with pytest.raises(ValueError, match="180"):
        timer.restore(
            id_prova=7,
            session_id=3,
            duration_seconds=420,
            interval_seconds=179,
            started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )


def test_restore_counts_only_remaining_interval_with_monotonic_clock():
    clock = FakeClock()
    timer = CourseRecognitionTimer(
        clock=clock,
        wall_clock=lambda: datetime(2026, 8, 4, 12, 8, tzinfo=timezone.utc),
    )
    timer.restore(
        id_prova=7,
        session_id=3,
        duration_seconds=420,
        interval_seconds=180,
        started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )

    clock.advance(119)
    assert timer.tick()["estado"] == "intervalo"

    clock.advance(1)
    assert timer.tick()["estado"] == "liberado"


def test_restore_cancelled_timer_waits_for_a_new_start():
    cancelled_at = datetime(2026, 8, 4, 12, 3, tzinfo=timezone.utc)
    timer = CourseRecognitionTimer(
        clock=FakeClock(),
        wall_clock=lambda: datetime(2026, 8, 4, 12, 8, tzinfo=timezone.utc),
    )

    state = timer.restore(
        id_prova=7,
        session_id=3,
        duration_seconds=420,
        interval_seconds=180,
        started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        cancelled_at=cancelled_at,
    )

    assert state["estado"] == "aguardando"
    assert state["id_prova"] is None
    assert state["cancelado_em"] == "2026-08-04T12:03:00+00:00"


def test_reset_returns_timer_to_waiting_and_notifies_once():
    timer = CourseRecognitionTimer(clock=FakeClock())
    changes = []
    timer.add_state_change_listener(changes.append)
    timer.start(7, 3, 420)

    reset = timer.reset()

    assert reset["estado"] == "aguardando"
    assert reset["id_prova"] is None
    assert reset["versao"] == 2
    assert len(changes) == 2
