import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
