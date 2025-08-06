from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from . import crud, models, schemas
from .database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

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

@app.put("/users/{user_id}", response_model=schemas.UserResponse)
def update_user(user_id: int, user: schemas.UserCreate, db: Session = Depends(get_db)):
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
def get_inscricoes_endpoint(db: Session = Depends(get_db)):
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