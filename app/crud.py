# app/crud.py

from datetime import datetime
from sqlalchemy.orm import Session
from .models import User, Competicao, Prova, Inscricao, Resultado
from .schemas import InscricaoCreate, InscricaoUpdate, ProvaCreate, ProvaUpdate, UserCreate, CompeticaoCreate, CompeticaoUpdate

# User 
def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()

def create_user(db: Session, user: UserCreate):
    db_user = User(name=user.name, email=user.email)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(db: Session, user_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    db.delete(user)
    db.commit()

# Competitions

def get_competition(db: Session, competition_id: int):
    return db.query(Competicao).filter(Competicao.id_competicao == competition_id).first()

def create_competition(db: Session, competition: CompeticaoCreate):
    db_competition = Competicao(
        nome=competition.nome,
        data=datetime.fromisoformat(competition.data),
        localizacao=competition.localizacao,
        nomes_arbitros_convidados=competition.nomes_arbitros_convidados,
        nome_diretor_evento=competition.nome_diretor_evento,
        nome_responsavel_secretaria=competition.nome_responsavel_secretaria,
        nome_veterinario=competition.nome_veterinario,
        responsavel_id=competition.responsavel_id
    )
    db.add(db_competition)
    db.commit()
    db.refresh(db_competition)
    return db_competition

def delete_competition(db: Session, competition_id: int):
    competition = db.query(Competicao).filter(Competicao.id_competicao == competition_id).first()
    db.delete(competition)
    db.commit()
    return {"message": "Competition deleted successfully"}

def update_competition(db: Session, competition_id: int, competition: CompeticaoUpdate):
    db_competition = db.query(Competicao).filter(Competicao.id_competicao == competition_id).first()
    if db_competition:
        for key, value in competition.model_dump(exclude_unset=True).items():
            setattr(db_competition, key, value)
        db.commit()
        db.refresh(db_competition)
        return db_competition
    return None

# Prova (Competition Event)

def create_prova(db: Session, prova: ProvaCreate):
    db_prova = Prova(**prova.model_dump())
    db.add(db_prova)
    db.commit()
    db.refresh(db_prova)
    return db_prova

def get_prova(db: Session, prova_id: int):
    return db.query(Prova).filter(Prova.id_prova == prova_id).first()

def get_provas(db: Session):
    return db.query(Prova).all()

def update_prova(db: Session, prova_id: int, prova_update: ProvaUpdate):
    db_prova = get_prova(db, prova_id)
    for key, value in prova_update.model_dump(exclude_unset=True).items():
        setattr(db_prova, key, value)
    db.commit()
    db.refresh(db_prova)
    return db_prova

def delete_prova(db: Session, prova_id: int):
    db_prova = get_prova(db, prova_id)
    db.delete(db_prova)
    db.commit()

# Inscricao (Registration)

def create_inscricao(db: Session, inscricao: InscricaoCreate):
    data = inscricao.model_dump()
    if data.get("hora_inicio"):
        data["hora_inicio"] = datetime.fromisoformat(data["hora_inicio"])
    db_inscricao = Inscricao(**data)
    db.add(db_inscricao)
    db.commit()
    db.refresh(db_inscricao)
    return db_inscricao

def get_inscricao(db: Session, inscricao_id: int):
    return db.query(Inscricao).filter(Inscricao.id_inscricao == inscricao_id).first()

def get_inscricoes(db: Session):
    return db.query(Inscricao).all()

def update_inscricao(db: Session, inscricao_id: int, inscricao_update: InscricaoUpdate):
    db_inscricao = get_inscricao(db, inscricao_id)
    for key, value in inscricao_update.model_dump(exclude_unset=True).items():
        setattr(db_inscricao, key, value)
    db.commit()
    db.refresh(db_inscricao)
    return db_inscricao

def delete_inscricao(db: Session, inscricao_id: int):
    db_inscricao = get_inscricao(db, inscricao_id)
    db.delete(db_inscricao)
    db.commit()