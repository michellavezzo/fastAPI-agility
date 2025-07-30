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
    