from pathlib import Path as FilePath
from typing import Optional
from datetime import datetime, timezone

import asyncio
import logging
import threading
from fastapi import FastAPI, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from sqlalchemy import inspect as sa_inspect, text

from . import crud, models, schemas
from .chrono import (
    Chronometer,
    IR_CALIBRATE_ON_STARTUP_DEFAULT,
    IR_CALIBRATION_APPLY_DEFAULT,
    IR_CALIBRATION_SAVE_DEFAULT,
)
from .course_recognition import CourseRecognitionTimer
from .ir_calibration import CalibrationError
from .database import SessionLocal, engine, get_db

models.Base.metadata.create_all(bind=engine)


def _sync_missing_columns():
    """Adiciona colunas que existem no modelo SQLAlchemy mas faltam no SQLite.
    Para colunas recém-adicionadas, preenche NULLs com o default do modelo.
    Substitui Alembic para prototipagem — seguro para desenvolvimento do TCC."""
    inspector = sa_inspect(engine)
    for table_name, table in models.Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in existing:
                col_type = col.type.compile(engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}'
                    ))
                    if col.default is not None:
                        default_val = col.default.arg
                        conn.execute(text(
                            f'UPDATE "{table_name}" SET "{col.name}" = :val WHERE "{col.name}" IS NULL'
                        ), {"val": default_val})
            else:
                # Backfill NULLs for columns that have a model default
                if col.default is not None:
                    with engine.begin() as conn:
                        conn.execute(text(
                            f'UPDATE "{table_name}" SET "{col.name}" = :val WHERE "{col.name}" IS NULL'
                        ), {"val": col.default.arg})


_sync_missing_columns()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_chrono = None
_course_recognition = None
_course_recognition_listener_timer = None
_course_recognition_task = None
_course_recognition_last_persisted_version = None
_course_recognition_start_lock = threading.RLock()
STATIC_DIR = FilePath(__file__).resolve().parent.parent / "static"


class ProvaAtivaWebSocketManager:
    def __init__(self):
        self._connections = set()
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._loop = None
        self._broadcast_pending = False

    def bind_loop(self):
        self._loop = asyncio.get_running_loop()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        await self._send_state(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)

    def schedule_state_broadcast(self, *_):
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._ensure_broadcast_task)

    def _ensure_broadcast_task(self):
        if self._broadcast_pending:
            return
        self._broadcast_pending = True
        asyncio.create_task(self.broadcast_state())

    async def _send_state(self, websocket: WebSocket):
        async with self._send_lock:
            await websocket.send_json({
                "tipo": "estado",
                "data": get_chrono().get_estado_completo(),
            })

    async def broadcast_state(self):
        self._broadcast_pending = False
        payload = {
            "tipo": "estado",
            "data": get_chrono().get_estado_completo(),
        }
        async with self._lock:
            connections = tuple(self._connections)

        stale_connections = []
        async with self._send_lock:
            for websocket in connections:
                try:
                    await websocket.send_json(payload)
                except Exception:
                    stale_connections.append(websocket)

        if stale_connections:
            async with self._lock:
                for websocket in stale_connections:
                    self._connections.discard(websocket)


ws_manager = ProvaAtivaWebSocketManager()


class CourseRecognitionWebSocketManager:
    def __init__(self):
        self._connections = set()
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._loop = None
        self._broadcast_pending = False

    def bind_loop(self):
        self._loop = asyncio.get_running_loop()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        await self._send_state(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)

    def schedule_state_broadcast(self, *_):
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._ensure_broadcast_task)

    def _ensure_broadcast_task(self):
        if self._broadcast_pending:
            return
        self._broadcast_pending = True
        asyncio.create_task(self.broadcast_state())

    async def _send_state(self, websocket: WebSocket):
        async with self._send_lock:
            await websocket.send_json({
                "tipo": "estado_reconhecimento",
                "data": get_course_recognition().get_state(),
            })

    async def broadcast_state(self):
        self._broadcast_pending = False
        payload = {
            "tipo": "estado_reconhecimento",
            "data": get_course_recognition().get_state(),
        }
        async with self._lock:
            connections = tuple(self._connections)
        stale_connections = []
        async with self._send_lock:
            for websocket in connections:
                try:
                    await websocket.send_json(payload)
                except Exception:
                    stale_connections.append(websocket)
        if stale_connections:
            async with self._lock:
                for websocket in stale_connections:
                    self._connections.discard(websocket)


course_recognition_ws_manager = CourseRecognitionWebSocketManager()


def get_chrono():
    global _chrono
    if _chrono is None:
        _chrono = Chronometer()
        _chrono.add_state_change_listener(ws_manager.schedule_state_broadcast)
    return _chrono


def get_course_recognition():
    global _course_recognition, _course_recognition_listener_timer
    if _course_recognition is None:
        _course_recognition = CourseRecognitionTimer()
    if _course_recognition_listener_timer is not _course_recognition:
        _course_recognition.add_state_change_listener(
            course_recognition_ws_manager.schedule_state_broadcast
        )
        _course_recognition_listener_timer = _course_recognition
    return _course_recognition


def restore_course_recognition():
    timer = get_course_recognition()
    with SessionLocal() as db:
        recognition = crud.get_latest_course_recognition(db)
        if recognition is None:
            return timer.get_state()
        state = timer.restore(
            id_prova=recognition.id_prova,
            session_id=recognition.id_reconhecimento,
            duration_seconds=recognition.duracao_segundos,
            interval_seconds=recognition.intervalo_segundos,
            started_at=crud.as_utc(recognition.iniciado_em),
            cancelled_at=(
                crud.as_utc(recognition.cancelado_em)
                if recognition.cancelado_em is not None
                else None
            ),
        )
        if state["id_reconhecimento"] is not None:
            crud.update_course_recognition_state(db, state)
        return state


async def course_recognition_tick_loop():
    global _course_recognition_last_persisted_version
    while True:
        state = get_course_recognition().tick()
        if state["versao"] != _course_recognition_last_persisted_version:
            with SessionLocal() as db:
                crud.update_course_recognition_state(db, state)
            _course_recognition_last_persisted_version = state["versao"]
        await asyncio.sleep(0.25)


@app.on_event("startup")
async def startup_event():
    global _course_recognition_task, _course_recognition_last_persisted_version
    ws_manager.bind_loop()
    course_recognition_ws_manager.bind_loop()
    recognition_state = restore_course_recognition()
    _course_recognition_last_persisted_version = recognition_state["versao"]
    if _course_recognition_task is None or _course_recognition_task.done():
        _course_recognition_task = asyncio.create_task(course_recognition_tick_loop())
    chrono = get_chrono()
    if IR_CALIBRATE_ON_STARTUP_DEFAULT:
        logging.info("AGILITY_IR_CALIBRATE_ON_STARTUP ativo. Calibrando sensor IR antes de liberar API.")
        try:
            chrono.calibrate_ir_sensor(
                apply=IR_CALIBRATION_APPLY_DEFAULT,
                save=IR_CALIBRATION_SAVE_DEFAULT,
                trigger="startup",
            )
        except Exception:
            logging.exception(
                "Calibracao IR de startup falhou. Backend continuara subindo com status de erro."
            )


@app.get("/hardware/estado")
def hardware_estado(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_chrono().get_hardware_status()


@app.get("/config/ir/status")
def config_ir_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_chrono().get_ir_config_status()


@app.post("/config/ir/calibracao")
def config_ir_calibracao(
    apply: bool = Query(True),
    save: bool = Query(True),
):
    try:
        return get_chrono().calibrate_ir_sensor(
            apply=apply,
            save=save,
            trigger="endpoint",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CalibrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


@app.websocket("/ws/prova-ativa")
async def websocket_prova_ativa(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)


@app.websocket("/ws/reconhecimento-pista")
async def websocket_reconhecimento_pista(websocket: WebSocket):
    await course_recognition_ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await course_recognition_ws_manager.disconnect(websocket)

#  Usuários

@app.post("/users/", response_model=schemas.UserResponse)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    return crud.create_user(db=db, user=user)

@app.get("/users/{user_id}", response_model=schemas.UserResponse)
def read_user(user_id: int, db: Session = Depends(get_db)):
    db_user = crud.get_user(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return db_user

@app.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    db_user = crud.get_user(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    crud.delete_user(db=db, user_id=user_id)
    return {"message": "Usuário deletado com sucesso"}

@app.get("/users/", response_model=list[schemas.UserResponse])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).all()

@app.put("/users/{user_id}", response_model=schemas.UserResponse)
def update_user(user_id: int, user: schemas.UserUpdate, db: Session = Depends(get_db)):
    db_user = crud.get_user(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return crud.update_user(db=db, user_id=user_id, user=user)

# Competições

@app.post("/competicoes/", response_model=schemas.CompeticaoResponse)
def create_competition(competition: schemas.CompeticaoCreate, db: Session = Depends(get_db)):
    return crud.create_competition(db=db, competition=competition)

@app.get("/competicoes/{competition_id}", response_model=schemas.CompeticaoResponse)
def read_competition(competition_id: int, db: Session = Depends(get_db)):
    db_competition = crud.get_competition(db, competition_id=competition_id)
    if db_competition is None:
        raise HTTPException(status_code=404, detail="Competição não encontrada")
    return db_competition

@app.get("/competicoes/", response_model=list[schemas.CompeticaoResponse])
def list_competitions(db: Session = Depends(get_db)):
    return db.query(models.Competicao).all()

@app.delete("/competicoes/{competition_id}")
def delete_competition(competition_id: int, db: Session = Depends(get_db)): 
    db_competition = crud.get_competition(db, competition_id=competition_id)
    if db_competition is None:
        raise HTTPException(status_code=404, detail="Competição não encontrada")
    return crud.delete_competition(db=db, competition_id=competition_id)

@app.put("/competicoes/{competition_id}", response_model=schemas.CompeticaoResponse)
def update_competition(competition_id: int, competition: schemas.CompeticaoUpdate, db: Session = Depends(get_db)):
    db_competition = crud.get_competition(db, competition_id=competition_id)
    if db_competition is None:
        raise HTTPException(status_code=404, detail="Competição não encontrada")
    return crud.update_competition(db=db, competition_id=competition_id, competition=competition)

#  Provas

@app.post("/provas/", response_model=schemas.ProvaResponse)
def create_prova_endpoint(prova: schemas.ProvaCreate, db: Session = Depends(get_db)):
    return crud.create_prova(db, prova)

@app.get("/provas/{prova_id}", response_model=schemas.ProvaResponse)
def get_prova_endpoint(prova_id: int, db: Session = Depends(get_db)):
    return crud.get_prova(db, prova_id)

@app.get("/provas/", response_model=list[schemas.ProvaResponse])
def get_provas_endpoint(db: Session = Depends(get_db)):
    return crud.get_provas(db)

@app.put("/provas/{prova_id}", response_model=schemas.ProvaResponse)
def update_prova_endpoint(prova_id: int, prova_update: schemas.ProvaUpdate, db: Session = Depends(get_db)):
    return crud.update_prova(db, prova_id, prova_update)

@app.delete("/provas/{prova_id}")
def delete_prova_endpoint(prova_id: int, db: Session = Depends(get_db)):
    crud.delete_prova(db, prova_id)
    return {"ok": True}

# Inscrições 

@app.post("/inscricoes/", response_model=schemas.InscricaoResponse)
def create_inscricao_endpoint(inscricao: schemas.InscricaoCreate, db: Session = Depends(get_db)):
    return crud.create_inscricao(db, inscricao) 

@app.get("/inscricoes/{inscricao_id}", response_model=schemas.InscricaoResponse)
def get_inscricao_endpoint(inscricao_id: int, db: Session = Depends(get_db)):
    return crud.get_inscricao(db, inscricao_id)

@app.get("/inscricoes/", response_model=list[schemas.InscricaoResponse])
def get_inscricoes_endpoint(status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    if status:
        return crud.get_inscricoes_por_status(db, status)
    return crud.get_inscricoes(db) 

@app.put("/inscricoes/{inscricao_id}", response_model=schemas.InscricaoResponse)
def update_inscricao_endpoint(inscricao_id: int, inscricao_update: schemas.InscricaoUpdate, db: Session = Depends(get_db)):
    return crud.update_inscricao(db, inscricao_id, inscricao_update)

@app.delete("/inscricoes/{inscricao_id}")
def delete_inscricao_endpoint(inscricao_id: int, db: Session = Depends(get_db)):
    crud.delete_inscricao(db, inscricao_id)
    return {"ok": True}

# Competidor
@app.post("/competidor/", response_model=schemas.CompetidorResponse)
def create_competidor_endpoint(competidor: schemas.CompetidorCreate, db: Session = Depends(get_db)):
    return crud.create_competidor(db, competidor)

@app.get("/competidor/{competidor_id}", response_model=schemas.CompetidorResponse)
def get_competidor_endpoint(competidor_id: int, db: Session = Depends(get_db)):
    return crud.get_competidor(db, competidor_id)

@app.get("/competidor/", response_model=list[schemas.CompetidorResponse])
def get_competidores_endpoint(db: Session = Depends(get_db)):
    return crud.get_competidores(db)

@app.put("/competidor/{competidor_id}", response_model=schemas.CompetidorResponse)
def update_competidor_endpoint(competidor_id: int, competidor_update: schemas.CompetidorUpdate, db: Session = Depends(get_db)):
    return crud.update_competidor(db, competidor_id, competidor_update)

@app.delete("/competidor/{competidor_id}")
def delete_competidor_endpoint(competidor_id: int, db: Session = Depends(get_db)):
    crud.delete_competidor(db, competidor_id)
    return {"ok": True}

# Cao
@app.post("/cao/", response_model=schemas.CaoResponse)
def create_cao_endpoint(cao: schemas.CaoCreate, db: Session = Depends(get_db)):
    return crud.create_cao(db, cao)

@app.get("/cao/{microchip}", response_model=schemas.CaoResponse)
def get_cao_endpoint(microchip: str, db: Session = Depends(get_db)):
    return crud.get_cao(db, microchip)

@app.get("/cao/", response_model=list[schemas.CaoResponse])
def get_caes_endpoint(db: Session = Depends(get_db)):
    return crud.get_caes(db)

@app.put("/cao/{microchip}", response_model=schemas.CaoResponse)
def update_cao_endpoint(microchip: str, cao_update: schemas.CaoUpdate, db: Session = Depends(get_db)):
    return crud.update_cao(db, microchip, cao_update)

@app.delete("/cao/{microchip}")
def delete_cao_endpoint(microchip: str, db: Session = Depends(get_db)):
    crud.delete_cao(db, microchip)
    return {"ok": True}

# Juiz
@app.post("/juiz/", response_model=schemas.JuizResponse)
def create_juiz_endpoint(juiz: schemas.JuizCreate, db: Session = Depends(get_db)):
    return crud.create_juiz(db, juiz)

@app.get("/juiz/{juiz_id}", response_model=schemas.JuizResponse)
def get_juiz_endpoint(juiz_id: int, db: Session = Depends(get_db)):
    return crud.get_juiz(db, juiz_id)

@app.get("/juiz/", response_model=list[schemas.JuizResponse])
def get_juizes_endpoint(db: Session = Depends(get_db)):
    return crud.get_juizes(db)

@app.put("/juiz/{juiz_id}", response_model=schemas.JuizResponse)
def update_juiz_endpoint(juiz_id: int, juiz_update: schemas.JuizUpdate, db: Session = Depends(get_db)):
    return crud.update_juiz(db, juiz_id, juiz_update)

@app.delete("/juiz/{juiz_id}")
def delete_juiz_endpoint(juiz_id: int, db: Session = Depends(get_db)):
    crud.delete_juiz(db, juiz_id)
    return {"ok": True}

# Resultado
@app.post("/resultados/", response_model=schemas.ResultadoResponse)
def create_resultado_endpoint(resultado: schemas.ResultadoCreate, db: Session = Depends(get_db)):
    return crud.create_resultado(db, resultado)

@app.get("/resultados/{resultado_id}", response_model=schemas.ResultadoResponse)
def get_resultado_endpoint(resultado_id: int, db: Session = Depends(get_db)):
    return crud.get_resultado(db, resultado_id)

@app.get("/resultados/", response_model=list[schemas.ResultadoResponse])
def get_resultados_endpoint(db: Session = Depends(get_db)):
    return crud.get_resultados(db)

@app.put("/resultados/{resultado_id}", response_model=schemas.ResultadoResponse)
def update_resultado_endpoint(resultado_id: int, resultado_update: schemas.ResultadoUpdate, db: Session = Depends(get_db)):
    return crud.update_resultado(db, resultado_id, resultado_update)

@app.delete("/resultados/{resultado_id}")
def delete_resultado_endpoint(resultado_id: int, db: Session = Depends(get_db)):
    crud.delete_resultado(db, resultado_id)
    return {"ok": True}

# Avaliacao
@app.post("/avaliacoes/", response_model=schemas.AvaliacaoResponse)
def create_avaliacao_endpoint(avaliacao: schemas.AvaliacaoCreate, db: Session = Depends(get_db)):
    return crud.create_avaliacao(db, avaliacao)

@app.get("/avaliacoes/{avaliacao_id}", response_model=schemas.AvaliacaoResponse)
def get_avaliacao_endpoint(avaliacao_id: int, db: Session = Depends(get_db)):
    return crud.get_avaliacao(db, avaliacao_id)

@app.get("/avaliacoes/", response_model=list[schemas.AvaliacaoResponse])
def get_avaliacoes_endpoint(db: Session = Depends(get_db)):
    return crud.get_avaliacoes(db)

@app.put("/avaliacoes/{avaliacao_id}", response_model=schemas.AvaliacaoResponse)
def update_avaliacao_endpoint(avaliacao_id: int, avaliacao_update: schemas.AvaliacaoUpdate, db: Session = Depends(get_db)):
    return crud.update_avaliacao(db, avaliacao_id, avaliacao_update)

@app.delete("/avaliacoes/{avaliacao_id}")
def delete_avaliacao_endpoint(avaliacao_id: int, db: Session = Depends(get_db)):
    crud.delete_avaliacao(db, avaliacao_id)
    return {"ok": True}

# Cronometragem
@app.post("/cronometros/", response_model=schemas.CronometragemResponse)
def create_cronometro_endpoint(cronometro: schemas.CronometragemCreate, db: Session = Depends(get_db)):
    return crud.create_cronometro(db, cronometro)

@app.get("/cronometros/{cronometro_id}", response_model=schemas.CronometragemResponse)
def get_cronometro_endpoint(cronometro_id: int, db: Session = Depends(get_db)):
    return crud.get_cronometro(db, cronometro_id)

@app.get("/cronometros/", response_model=list[schemas.CronometragemResponse])
def get_cronometros_endpoint(db: Session = Depends(get_db)):
    return crud.get_cronometros(db)

@app.put("/cronometros/{cronometro_id}", response_model=schemas.CronometragemResponse)
def update_cronometro_endpoint(cronometro_id: int, cronometro_update: schemas.CronometragemUpdate, db: Session = Depends(get_db)):
    return crud.update_cronometro(db, cronometro_id, cronometro_update)

@app.delete("/cronometros/{cronometro_id}")
def delete_cronometro_endpoint(cronometro_id: int, db: Session = Depends(get_db)):
    crud.delete_cronometro(db, cronometro_id)
    return {"ok": True}


# ────────────────── Prova Ativa (cronômetro em tempo real) ──────────────────

@app.post("/reconhecimento-pista/iniciar", response_model=schemas.ReconhecimentoPistaEstado)
def iniciar_reconhecimento_pista(
    body: schemas.ReconhecimentoPistaIniciar,
    db: Session = Depends(get_db),
):
    with _course_recognition_start_lock:
        prova = crud.get_prova(db, body.id_prova)
        if prova is None:
            raise HTTPException(status_code=404, detail="Prova não encontrada")
        timer = get_course_recognition()
        if timer.get_state()["estado"] != "aguardando":
            raise HTTPException(
                status_code=409,
                detail="Uma sessão de reconhecimento de pista deve ser limpa antes de iniciar outra.",
            )
        recognition = crud.create_course_recognition(
            db,
            id_prova=body.id_prova,
            duration_seconds=body.duracao_segundos,
            started_at=datetime.now(timezone.utc),
        )
        try:
            return timer.start(
                id_prova=body.id_prova,
                session_id=recognition.id_reconhecimento,
                duration_seconds=body.duracao_segundos,
                started_at=crud.as_utc(recognition.iniciado_em),
            )
        except Exception:
            db.delete(recognition)
            db.commit()
            raise


@app.get("/reconhecimento-pista/estado", response_model=schemas.ReconhecimentoPistaEstado)
def estado_reconhecimento_pista(response: Response):
    _no_store(response)
    return get_course_recognition().tick()


@app.post("/reconhecimento-pista/cancelar", response_model=schemas.ReconhecimentoPistaEstado)
def cancelar_reconhecimento_pista(db: Session = Depends(get_db)):
    timer = get_course_recognition()
    active_state = timer.get_state()
    state = timer.cancel()
    if active_state["id_reconhecimento"] is not None:
        crud.cancel_course_recognition(
            db,
            session_id=active_state["id_reconhecimento"],
            cancelled_at=state["cancelado_em"],
        )
    return state


@app.post("/reconhecimento-pista/reset", response_model=schemas.ReconhecimentoPistaEstado)
def resetar_reconhecimento_pista(db: Session = Depends(get_db)):
    timer = get_course_recognition()
    active_state = timer.get_state()
    if active_state["estado"] in ("reconhecimento", "intervalo"):
        raise HTTPException(
            status_code=409,
            detail="Reconhecimento de pista em andamento deve ser cancelado antes do reset.",
        )
    if active_state["id_reconhecimento"] is not None:
        # Reset invalida operacionalmente uma liberação concluída para que ela não sobreviva ao reinício.
        crud.cancel_course_recognition(
            db,
            session_id=active_state["id_reconhecimento"],
            cancelled_at=datetime.now(timezone.utc).isoformat(),
        )
    return timer.reset()

@app.post("/prova-ativa/preparar", response_model=schemas.ProvaAtivaEstado)
def preparar_prova(body: schemas.ProvaAtivaPreparar, db: Session = Depends(get_db)):
    insc = crud.get_inscricao(db, body.id_inscricao)
    if not insc:
        raise HTTPException(status_code=404, detail="Inscrição não encontrada")
    dados = {}
    dados["id_prova"] = insc.id_prova
    if insc.competidor:
        dados["competidor_nome"] = insc.competidor.nome
    if insc.cao:
        dados["cao_nome"] = insc.cao.nome
        dados["cao_raca"] = insc.cao.raca
    dados["colete_competidor"] = insc.colete_competidor
    if insc.prova:
        dados["categoria"] = insc.prova.categoria
        dados["classe"] = insc.prova.classe
        dados["num_obstaculos"] = insc.prova.num_obstaculos
        dados["comprimento_pista"] = insc.prova.comprimento_pista
        dados["tsp"] = insc.prova.tsp
        dados["tmp"] = insc.prova.tmp
    chrono = get_chrono()
    chrono.prepare(body.id_inscricao, dados)
    return chrono.get_estado_completo()


@app.post("/prova-ativa/autorizar", response_model=schemas.ProvaAtivaEstado)
def autorizar_prova():
    chrono = get_chrono()

    def validate_course_recognition(id_prova):
        recognition = get_course_recognition()
        recognition_state = recognition.tick()
        if not (
            recognition_state["estado"] == "liberado"
            and recognition_state["id_prova"] == id_prova
        ):
            remaining_field = {
                "reconhecimento": "reconhecimento_restante",
                "intervalo": "intervalo_restante",
            }.get(recognition_state["estado"])
            if remaining_field is not None:
                remaining = recognition_state.get(remaining_field)
            else:
                remaining = None
            remaining_detail = "sem sessão ativa" if remaining is None else f"{remaining:.0f}s restantes"
            return (
                "Reconhecimento de pista não liberado para a prova "
                f"{id_prova}: estado {recognition_state['estado']} ({remaining_detail})."
            )
        return None

    if not chrono.autorizar(validate_course_recognition):
        raise HTTPException(
            status_code=409,
            detail=chrono.get_last_authorize_error() or "Estado inválido para autorizar",
        )
    return chrono.get_estado_completo()


@app.get("/prova-ativa/estado", response_model=schemas.ProvaAtivaEstado)
def estado_prova(response: Response):
    _no_store(response)
    return get_chrono().get_estado_completo()


@app.post("/prova-ativa/falta", response_model=schemas.FaltasRecusasResponse)
def add_falta():
    chrono = get_chrono()
    if not chrono.add_falta():
        raise HTTPException(status_code=409, detail="Estado inválido para adicionar falta")
    e = chrono.get_estado_completo()
    return {"faltas": e["faltas"], "recusas": e["recusas"]}


@app.post("/prova-ativa/desfazer-falta", response_model=schemas.FaltasRecusasResponse)
def remove_falta():
    chrono = get_chrono()
    if not chrono.remove_falta():
        raise HTTPException(status_code=409, detail="Não é possível remover falta")
    e = chrono.get_estado_completo()
    return {"faltas": e["faltas"], "recusas": e["recusas"]}


@app.post("/prova-ativa/recusa", response_model=schemas.FaltasRecusasResponse)
def add_recusa():
    chrono = get_chrono()
    if not chrono.add_recusa():
        raise HTTPException(status_code=409, detail="Estado inválido para adicionar recusa")
    e = chrono.get_estado_completo()
    return {"faltas": e["faltas"], "recusas": e["recusas"]}


@app.post("/prova-ativa/desfazer-recusa", response_model=schemas.FaltasRecusasResponse)
def remove_recusa():
    chrono = get_chrono()
    if not chrono.remove_recusa():
        raise HTTPException(status_code=409, detail="Não é possível remover recusa")
    e = chrono.get_estado_completo()
    return {"faltas": e["faltas"], "recusas": e["recusas"]}


@app.post("/prova-ativa/forcar-fim", response_model=schemas.ProvaAtivaEstado)
def forcar_fim():
    chrono = get_chrono()
    if not chrono.forcar_fim():
        raise HTTPException(status_code=409, detail="Estado inválido para forçar fim")
    return chrono.get_estado_completo()


@app.post("/prova-ativa/confirmar")
def confirmar_prova(db: Session = Depends(get_db)):
    chrono = get_chrono()
    dados = chrono.get_dados_confirmacao()
    if dados is None:
        raise HTTPException(status_code=409, detail="Prova não está finalizada")

    # Persiste cronometragem TOP
    crono_top = schemas.CronometragemCreate(
        id_inscricao=dados["id_inscricao"],
        tempo_inicial=dados["hora_inicio_prova"],
        tempo_final=dados["hora_fim_prova"],
        status="finalizado",
        tipo="prova",
        tempo_oficial=dados["top"],
    )
    crud.create_cronometro(db, crono_top)

    # Persiste cronometragem TIA
    crono_tia = schemas.CronometragemCreate(
        id_inscricao=dados["id_inscricao"],
        tempo_inicial=dados["hora_autorizacao"],
        tempo_final=dados["hora_inicio_prova"],
        status="finalizado",
        tipo="tia",
        tempo_oficial=dados["tia"],
    )
    crud.create_cronometro(db, crono_tia)

    # Atualiza inscrição
    inscricao_update = schemas.InscricaoUpdate(
        tempo_prova=dados["top"],
        faltas_prova=dados["faltas"],
        recusas_prova=dados["recusas"],
        status="finalizado",
    )
    crud.update_inscricao(db, dados["id_inscricao"], inscricao_update)

    chrono.reset()
    return {"ok": True, "top": dados["top"], "tia": dados["tia"]}


@app.post("/prova-ativa/reset", response_model=schemas.ProvaAtivaEstado)
def reset_prova():
    chrono = get_chrono()
    chrono.reset()
    return chrono.get_estado_completo()


@app.post("/prova-ativa/simular-sensor", response_model=schemas.ProvaAtivaEstado)
def simular_sensor():
    chrono = get_chrono()
    chrono.simular_acionamento()
    return chrono.get_estado_completo()


# ────────────────── Interfaces HTML ──────────────────

@app.get("/painel", response_class=HTMLResponse)
def painel():
    html_path = STATIC_DIR / "painel.html"
    if not html_path.is_file():
        raise HTTPException(status_code=404, detail="painel.html não encontrado")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/operador", response_class=HTMLResponse)
def operador():
    html_path = STATIC_DIR / "operador.html"
    if not html_path.is_file():
        raise HTTPException(status_code=404, detail="operador.html não encontrado")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.on_event("shutdown")
async def shutdown_event():
    global _course_recognition_task
    if _course_recognition_task is not None:
        _course_recognition_task.cancel()
        try:
            await _course_recognition_task
        except asyncio.CancelledError:
            pass
        _course_recognition_task = None
    if _chrono is not None:
        _chrono.cleanup()
