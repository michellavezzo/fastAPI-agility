# app/schemas.py
from typing import Optional
from pydantic import BaseModel, field_validator

# User Schemas
class UserCreate(BaseModel):
    name: str
    email: str
class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    class Config:
        orm_mode = True

# Competition Schemas
class CompeticaoCreate(BaseModel):
    nome: str
    data: str  # Use str for date in ISO format
    localizacao: str
    nomes_arbitros_convidados: str = None
    nome_diretor_evento: str = None
    nome_responsavel_secretaria: str = None
    nome_veterinario: str = None
    responsavel_id: int

class CompeticaoUpdate(BaseModel):
    nome: str = None
    data: str = None
    localizacao: str = None
    nomes_arbitros_convidados: str = None
    nome_diretor_evento: str = None
    nome_responsavel_secretaria: str = None
    nome_veterinario: str = None

class CompeticaoResponse(BaseModel):
    id_competicao: int
    nome: str
    data: str
    localizacao: str
    nomes_arbitros_convidados: Optional[str] = None
    nome_diretor_evento: Optional[str] = None
    nome_responsavel_secretaria: Optional[str] = None
    nome_veterinario: Optional[str] = None
    responsavel_id: int

    @field_validator("data", mode="before")
    def date_to_str(cls, v):
        if isinstance(v, (str, type(None))):
            return v
        return v.isoformat()

    class Config:
        from_attributes = True

# Prova Schemas
class ProvaCreate(BaseModel):
    categoria: str
    classe: str
    num_obstaculos: int
    tsp: float
    tmp: float
    vel_media_necessaria: float
    comprimento_pista: int
    descricao: Optional[str] = None
    id_competicao: int

class ProvaUpdate(BaseModel):
    categoria: Optional[str] = None
    classe: Optional[str] = None
    num_obstaculos: Optional[int] = None
    tsp: Optional[int] = None
    tmp: Optional[int] = None
    vel_media_necessaria: Optional[float] = None
    comprimento_pista: Optional[int] = None
    descricao: Optional[str] = None

class ProvaResponse(BaseModel):
    id_prova: int
    categoria: str
    classe: str
    num_obstaculos: int
    tsp: int
    tmp: int
    vel_media_necessaria: float
    comprimento_pista: int
    descricao: Optional[str] = None
    id_competicao: int
    criado_em: str
    atualizado_em: Optional[str] = None

    @field_validator("criado_em", "atualizado_em", mode="before")
    def dt_to_str(cls, v):
        if isinstance(v, (str, type(None))):
            return v
        return v.isoformat()

    class Config:
        from_attributes = True