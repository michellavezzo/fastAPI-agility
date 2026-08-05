import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import main, models
from app.chrono import Chronometer
from app.course_recognition import CourseRecognitionTimer
from app.database import get_db


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    models.Base.metadata.create_all(engine)

    def override_get_db():
        with TestingSession() as db:
            yield db

    clock = FakeClock()
    timer = CourseRecognitionTimer(clock=clock)
    monkeypatch.setattr(main, "_course_recognition", timer, raising=False)
    monkeypatch.setattr(main, "_course_recognition_listener_timer", None, raising=False)
    monkeypatch.setattr(main, "_course_recognition_last_persisted_version", None, raising=False)
    monkeypatch.setattr(main, "_course_recognition_task", None, raising=False)
    monkeypatch.setattr(main, "_chrono", Chronometer(sensor_require_ready=False))
    monkeypatch.setattr(main, "SessionLocal", TestingSession, raising=False)
    main.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main.app) as test_client:
        yield test_client, TestingSession, timer, clock
    main.app.dependency_overrides.clear()


def test_start_rejects_duration_below_regulatory_minimum(client):
    http, _, _, _ = client

    response = http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": 1, "duracao_segundos": 419},
    )

    assert response.status_code == 422


@pytest.fixture
def prova(client):
    _, Session, _, _ = client
    with Session() as db:
        prova = models.Prova(
            categoria="Standard",
            classe="A1",
            num_obstaculos=15,
            tsp=60.0,
            tmp=75.0,
            vel_media_necessaria=3.0,
            comprimento_pista=180,
        )
        db.add(prova)
        db.commit()
        db.refresh(prova)
        return prova


def test_start_persists_recognition_session_by_prova(client, prova):
    http, Session, _, _ = client

    response = http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    )

    assert response.status_code == 200
    assert response.json()["estado"] == "reconhecimento"
    with Session() as db:
        row = db.query(models.ReconhecimentoPista).one()
        assert row.id_prova == prova.id_prova
        assert row.duracao_segundos == 420
        assert row.intervalo_segundos == 180
        assert row.status == "reconhecimento"


def test_cancel_is_persisted_and_state_is_waiting(client, prova):
    http, Session, _, _ = client
    http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    )

    cancelled = http.post("/reconhecimento-pista/cancelar")

    assert cancelled.status_code == 200
    assert cancelled.json()["estado"] == "aguardando"
    with Session() as db:
        assert db.query(models.ReconhecimentoPista).one().status == "cancelado"
    assert http.get("/reconhecimento-pista/estado").json()["estado"] == "aguardando"


@pytest.fixture
def inscricao(client, prova):
    _, Session, _, _ = client
    with Session() as db:
        competidor = models.Competidor(nome="Ana", escola="Clube")
        cao = models.Cao(
            microchip="123",
            nome="Bidu",
            raca="Border Collie",
            cernelha="50",
            categoria_salto="Large",
        )
        db.add_all([competidor, cao])
        db.flush()
        inscricao = models.Inscricao(
            id_prova=prova.id_prova,
            id_competidor=competidor.id_competidor,
            microchip_cao=cao.microchip,
            colete_competidor="7",
        )
        db.add(inscricao)
        db.commit()
        db.refresh(inscricao)
        return inscricao


def test_authorization_requires_released_recognition_for_prepared_prova(client, prova, inscricao):
    http, Session, timer, clock = client
    assert http.post(
        "/prova-ativa/preparar", json={"id_inscricao": inscricao.id_inscricao}
    ).status_code == 200

    absent = http.post("/prova-ativa/autorizar")
    assert absent.status_code == 409
    assert "reconhecimento" in absent.json()["detail"].lower()

    with Session() as db:
        other_prova = models.Prova(
            categoria="Jumping",
            classe="A1",
            num_obstaculos=12,
            tsp=50.0,
            tmp=65.0,
            vel_media_necessaria=3.0,
            comprimento_pista=150,
        )
        db.add(other_prova)
        db.commit()
        db.refresh(other_prova)

    timer.start(other_prova.id_prova, session_id=10, duration_seconds=420)
    clock.advance(600)
    timer.tick()
    different_prova = http.post("/prova-ativa/autorizar")
    assert different_prova.status_code == 409
    assert "prova" in different_prova.json()["detail"].lower()

    timer.start(prova.id_prova, session_id=11, duration_seconds=420)
    clock.advance(600)
    timer.tick()
    assert http.post("/prova-ativa/autorizar").status_code == 200


def test_authorization_reports_interval_time_remaining(client, prova, inscricao):
    http, _, timer, clock = client
    assert http.post(
        "/prova-ativa/preparar", json={"id_inscricao": inscricao.id_inscricao}
    ).status_code == 200
    timer.start(prova.id_prova, session_id=12, duration_seconds=420)
    clock.advance(420)
    assert timer.tick()["estado"] == "intervalo"

    response = http.post("/prova-ativa/autorizar")

    assert response.status_code == 409
    assert "180s restantes" in response.json()["detail"]


def test_authorization_validation_and_transition_are_atomic_against_prepare(
    client, prova, inscricao, monkeypatch
):
    http, Session, timer, clock = client
    assert http.post(
        "/prova-ativa/preparar", json={"id_inscricao": inscricao.id_inscricao}
    ).status_code == 200

    with Session() as db:
        other_prova = models.Prova(
            categoria="Jumping",
            classe="A1",
            num_obstaculos=12,
            tsp=50.0,
            tmp=65.0,
            vel_media_necessaria=3.0,
            comprimento_pista=150,
        )
        other_competidor = models.Competidor(nome="Bruno", escola="Clube")
        other_cao = models.Cao(
            microchip="456",
            nome="Lua",
            raca="Shetland Sheepdog",
            cernelha="35",
            categoria_salto="Small",
        )
        db.add_all([other_prova, other_competidor, other_cao])
        db.flush()
        other_inscricao = models.Inscricao(
            id_prova=other_prova.id_prova,
            id_competidor=other_competidor.id_competidor,
            microchip_cao=other_cao.microchip,
            colete_competidor="8",
        )
        db.add(other_inscricao)
        db.commit()
        other_prova_id = other_prova.id_prova
        other_inscricao_id = other_inscricao.id_inscricao

    timer.start(prova.id_prova, session_id=13, duration_seconds=420)
    clock.advance(600)
    assert timer.tick()["estado"] == "liberado"

    chrono = main.get_chrono()
    transitions = []
    original_mark_state_changed = chrono._mark_state_changed_locked

    def record_state_change():
        transitions.append((chrono._estado, chrono._dados_inscricao.get("id_prova")))
        original_mark_state_changed()

    monkeypatch.setattr(chrono, "_mark_state_changed_locked", record_state_change)

    validation_entered = Event()
    allow_validation_to_finish = Event()
    original_tick = timer.tick

    def blocked_tick():
        state = original_tick()
        validation_entered.set()
        assert allow_validation_to_finish.wait(timeout=2)
        return state

    monkeypatch.setattr(timer, "tick", blocked_tick)

    prepare_entered = Event()
    prepare_completed = Event()
    original_prepare = chrono.prepare

    def tracked_prepare(id_inscricao, dados):
        if id_inscricao == other_inscricao_id:
            prepare_entered.set()
        result = original_prepare(id_inscricao, dados)
        if id_inscricao == other_inscricao_id:
            prepare_completed.set()
        return result

    monkeypatch.setattr(chrono, "prepare", tracked_prepare)

    with ThreadPoolExecutor(max_workers=2) as executor:
        authorization = executor.submit(http.post, "/prova-ativa/autorizar")
        assert validation_entered.wait(timeout=2)
        preparation = executor.submit(
            http.post,
            "/prova-ativa/preparar",
            json={"id_inscricao": other_inscricao_id},
        )
        assert prepare_entered.wait(timeout=2)
        prepare_finished_during_validation = prepare_completed.wait(timeout=0.2)
        allow_validation_to_finish.set()
        authorization_response = authorization.result(timeout=3)
        preparation_response = preparation.result(timeout=3)

    assert prepare_finished_during_validation is False
    assert authorization_response.status_code == 200
    assert preparation_response.status_code == 200
    assert transitions[:2] == [
        ("autorizado", prova.id_prova),
        ("preparado", other_prova_id),
    ]


def test_websocket_sends_authoritative_recognition_snapshot(client):
    http, _, _, _ = client

    with http.websocket_connect("/ws/reconhecimento-pista") as websocket:
        message = websocket.receive_json()

    assert message["tipo"] == "estado_reconhecimento"
    assert message["data"]["estado"] == "aguardando"


def test_websocket_broadcasts_recognition_state_changes(client):
    http, _, timer, _ = client

    with http.websocket_connect("/ws/reconhecimento-pista") as websocket:
        websocket.receive_json()
        timer.start(id_prova=1, session_id=1, duration_seconds=420)
        message = websocket.receive_json()

    assert message["tipo"] == "estado_reconhecimento"
    assert message["data"]["estado"] == "reconhecimento"


def test_background_tick_persists_only_the_interval_transition(client, prova):
    http, Session, _, clock = client
    assert http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    ).status_code == 200

    clock.advance(420)
    time.sleep(0.3)

    with Session() as db:
        row = db.query(models.ReconhecimentoPista).one()
        assert row.status == "intervalo"
        assert row.reconhecimento_finalizado_em is not None


def test_startup_restores_naive_sqlite_timestamp_as_utc(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'restore.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    models.Base.metadata.create_all(engine)
    started_at = datetime.now(timezone.utc) - timedelta(seconds=480)
    with TestingSession() as db:
        prova = models.Prova(
            categoria="Standard",
            classe="A1",
            num_obstaculos=15,
            tsp=60.0,
            tmp=75.0,
            vel_media_necessaria=3.0,
            comprimento_pista=180,
        )
        db.add(prova)
        db.flush()
        db.add(models.ReconhecimentoPista(
            id_prova=prova.id_prova,
            duracao_segundos=420,
            intervalo_segundos=180,
            status="reconhecimento",
            iniciado_em=started_at.replace(tzinfo=None),
        ))
        db.commit()

    monkeypatch.setattr(main, "SessionLocal", TestingSession, raising=False)
    monkeypatch.setattr(
        main,
        "_course_recognition",
        CourseRecognitionTimer(clock=FakeClock()),
        raising=False,
    )
    monkeypatch.setattr(main, "_chrono", Chronometer(sensor_require_ready=False))
    with TestClient(main.app) as http:
        state = http.get("/reconhecimento-pista/estado").json()

    assert state["estado"] == "intervalo"
    assert state["id_prova"] == 1


def test_reset_rejects_an_active_recognition(client, prova):
    http, _, _, _ = client
    assert http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    ).status_code == 200

    response = http.post("/reconhecimento-pista/reset")

    assert response.status_code == 409
    assert "andamento" in response.json()["detail"].lower()


def test_authorization_without_prepared_enrollment_keeps_chronometer_error(client):
    http, _, _, _ = client

    response = http.post("/prova-ativa/autorizar")

    assert response.status_code == 409
    assert response.json()["detail"] == "Estado inválido para autorizar"


def test_start_rejects_when_the_previous_recognition_is_released(client, prova):
    http, Session, timer, clock = client
    timer.start(prova.id_prova, session_id=99, duration_seconds=420)
    clock.advance(600)
    timer.tick()

    response = http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    )

    assert response.status_code == 409
    with Session() as db:
        assert db.query(models.ReconhecimentoPista).count() == 0


def test_concurrent_starts_create_exactly_one_recognition(client, prova, monkeypatch):
    http, Session, _, _ = client
    original_create = main.crud.create_course_recognition
    first_create_entered = Event()
    allow_first_create = Event()
    calls = 0

    def delayed_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_create_entered.set()
            assert allow_first_create.wait(timeout=2)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(main.crud, "create_course_recognition", delayed_create)
    payload = {"id_prova": prova.id_prova, "duracao_segundos": 420}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(http.post, "/reconhecimento-pista/iniciar", json=payload)
        assert first_create_entered.wait(timeout=2)
        second = executor.submit(http.post, "/reconhecimento-pista/iniciar", json=payload)
        allow_first_create.set()
        statuses = sorted([first.result(timeout=3).status_code, second.result(timeout=3).status_code])

    assert statuses == [200, 409]
    with Session() as db:
        assert db.query(models.ReconhecimentoPista).count() == 1


def test_background_tick_persists_the_released_transition(client, prova):
    http, Session, _, clock = client
    assert http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    ).status_code == 200

    clock.advance(600)
    time.sleep(0.3)

    with Session() as db:
        row = db.query(models.ReconhecimentoPista).one()
        assert row.status == "liberado"
        assert row.liberado_em is not None


def test_reset_of_released_recognition_is_terminal_after_restart(client, prova, monkeypatch):
    http, Session, _, clock = client
    assert http.post(
        "/reconhecimento-pista/iniciar",
        json={"id_prova": prova.id_prova, "duracao_segundos": 420},
    ).status_code == 200
    clock.advance(600)
    time.sleep(0.3)

    assert http.post("/reconhecimento-pista/reset").json()["estado"] == "aguardando"
    with Session() as db:
        row = db.query(models.ReconhecimentoPista).one()
        assert row.status == "cancelado"
        assert row.cancelado_em is not None

    monkeypatch.setattr(main, "_course_recognition", CourseRecognitionTimer(clock=FakeClock()))
    monkeypatch.setattr(main, "_course_recognition_listener_timer", None, raising=False)
    assert main.restore_course_recognition()["estado"] == "aguardando"


def test_newest_cancelled_session_never_restores_an_older_release(client, prova, monkeypatch):
    _, Session, _, _ = client
    now = datetime.now(timezone.utc)
    with Session() as db:
        older = models.ReconhecimentoPista(
            id_prova=prova.id_prova,
            duracao_segundos=420,
            intervalo_segundos=180,
            status="liberado",
            iniciado_em=now - timedelta(seconds=600),
            reconhecimento_finalizado_em=now - timedelta(seconds=180),
            liberado_em=now,
        )
        newer = models.ReconhecimentoPista(
            id_prova=prova.id_prova,
            duracao_segundos=420,
            intervalo_segundos=180,
            status="cancelado",
            iniciado_em=now,
            cancelado_em=now,
        )
        db.add_all([older, newer])
        db.commit()

    monkeypatch.setattr(main, "_course_recognition", CourseRecognitionTimer(clock=FakeClock()))
    monkeypatch.setattr(main, "_course_recognition_listener_timer", None, raising=False)
    assert main.restore_course_recognition()["estado"] == "aguardando"


def test_restore_immediately_synchronizes_expired_session_to_database(client, prova, monkeypatch):
    _, Session, _, _ = client
    now = datetime.now(timezone.utc)
    with Session() as db:
        recognition = models.ReconhecimentoPista(
            id_prova=prova.id_prova,
            duracao_segundos=420,
            intervalo_segundos=180,
            status="reconhecimento",
            iniciado_em=now - timedelta(seconds=600),
        )
        db.add(recognition)
        db.commit()
        recognition_id = recognition.id_reconhecimento

    monkeypatch.setattr(main, "_course_recognition", CourseRecognitionTimer(clock=FakeClock()))
    monkeypatch.setattr(main, "_course_recognition_listener_timer", None, raising=False)
    restored = main.restore_course_recognition()

    assert restored["estado"] == "liberado"
    with Session() as db:
        row = db.get(models.ReconhecimentoPista, recognition_id)
        assert row.status == "liberado"
        assert row.reconhecimento_finalizado_em is not None
        assert row.liberado_em is not None


def test_prepared_state_exposes_its_prova_id(client, prova, inscricao):
    http, _, _, _ = client

    response = http.post("/prova-ativa/preparar", json={"id_inscricao": inscricao.id_inscricao})

    assert response.status_code == 200
    assert response.json()["id_prova"] == prova.id_prova


def test_persisted_recognition_enforces_regulatory_database_constraints(client, prova):
    _, Session, _, _ = client
    with Session() as db:
        db.add(models.ReconhecimentoPista(
            id_prova=prova.id_prova,
            duracao_segundos=419,
            intervalo_segundos=179,
            status="aguardando",
            iniciado_em=datetime.now(timezone.utc),
        ))
        with pytest.raises(IntegrityError):
            db.commit()
