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

# Inscricao Schemas

class InscricaoCreate(BaseModel):
    id_prova: int
    id_competidor: int
    microchip_cao: str
    colete_competidor: str
    tempo_prova: Optional[float] = None
    faltas_prova: Optional[int] = None
    recusas_prova: Optional[int] = None
    vel_media: Optional[float] = None
    pontuacao: Optional[int] = None
    hora_inicio: Optional[str] = None  # Use str for datetime in ISO format
    status: str = "pendente"

class InscricaoUpdate(BaseModel):
    id_prova: Optional[int] = None
    id_competidor: Optional[int] = None
    microchip_cao: Optional[str] = None
    colete_competidor: Optional[str] = None
    tempo_prova: Optional[float] = None
    faltas_prova: Optional[int] = None
    recusas_prova: Optional[int] = None
    vel_media: Optional[float] = None
    pontuacao: Optional[int] = None
    hora_inicio: Optional[str] = None  # Use str for datetime in ISO format
    status: Optional[str] = None

class InscricaoResponse(BaseModel):
    id_inscricao: int
    id_prova: int
    id_competidor: int
    microchip_cao: str
    colete_competidor: str
    tempo_prova: Optional[float] = None
    faltas_prova: Optional[int] = None
    recusas_prova: Optional[int] = None
    vel_media: Optional[float] = None
    pontuacao: Optional[int] = None
    hora_inicio: Optional[str] = None  # Use str for datetime in ISO format
    status: str

    @field_validator("hora_inicio", mode="before")
    def dt_to_str(cls, v):
        if isinstance(v, (str, type(None))):
            return v
        return v.isoformat()

    class Config:
        from_attributes = True

# Competidor
class CompetidorCreate(BaseModel):
    nome: str
    escola: str

class CompetidorUpdate(BaseModel):
    nome: Optional[str] = None
    escola: Optional[str] = None

class CompetidorResponse(BaseModel):
    id_competidor: int
    nome: str
    escola: str
    class Config:
        from_attributes = True

# Cao
class CaoCreate(BaseModel):
    microchip: str
    nome: str
    raca: str
    cernelha: str
    categoria_salto: str
    is_cao_branco: bool = False

class CaoUpdate(BaseModel):
    nome: Optional[str] = None
    raca: Optional[str] = None
    cernelha: Optional[str] = None
    categoria_salto: Optional[str] = None
    is_cao_branco: Optional[bool] = None

class CaoResponse(BaseModel):
    microchip: str
    nome: str
    raca: str
    cernelha: str
    categoria_salto: str
    is_cao_branco: bool
    class Config:
        from_attributes = True

# Juiz
class JuizCreate(BaseModel):
    nome: str
    email: str

class JuizUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None

class JuizResponse(BaseModel):
    id_juiz: int
    nome: str
    email: str
    class Config:
        from_attributes = True

# Resultado
class ResultadoCreate(BaseModel):
    id_inscricao: int
    posicao: Optional[int] = None
    total_pontos_t: int
    total_pontos_tp: int

class ResultadoUpdate(BaseModel):
    id_inscricao: Optional[int] = None
    posicao: Optional[int] = None
    total_pontos_t: Optional[int] = None
    total_pontos_tp: Optional[int] = None

class ResultadoResponse(BaseModel):
    id_resultado: int
    id_inscricao: int
    posicao: Optional[int] = None  # Permite nulo
    total_pontos_t: Optional[int] = None
    total_pontos_tp: Optional[int] = None
    class Config:
        from_attributes = True

# Avaliacao
class AvaliacaoCreate(BaseModel):
    id_prova: int
    id_juiz: int
    diretor_prova: str
    comentarios: Optional[str] = None

class AvaliacaoUpdate(BaseModel):
    id_prova: Optional[int] = None
    id_juiz: Optional[int] = None
    diretor_prova: Optional[str] = None
    comentarios: Optional[str] = None

class AvaliacaoResponse(BaseModel):
    id_avaliacao: int
    id_prova: int
    id_juiz: int
    diretor_prova: str
    comentarios: Optional[str] = None
    criado_em: Optional[str] = None
    atualizado_em: Optional[str] = None

    @field_validator("criado_em", "atualizado_em", mode="before")
    def dt_to_str(cls, v):
        if isinstance(v, (str, type(None))):
            return v
        return v.isoformat()

    class Config:
        from_attributes = True

# Cronometragem
class CronometragemCreate(BaseModel):
    id_inscricao: int
    tempo_inicial: str  # ISO format
    tempo_final: Optional[str] = None
    status: str = "parado"
    tempo_oficial: Optional[float] = None

class CronometragemUpdate(BaseModel):
    id_inscricao: Optional[int] = None
    tempo_inicial: Optional[str] = None
    tempo_final: Optional[str] = None
    status: Optional[str] = None
    tempo_oficial: Optional[float] = None

class CronometragemResponse(BaseModel):
    id_cronometro: int
    id_inscricao: int
    tempo_inicial: str
    tempo_final: Optional[str] = None
    status: str
    tempo_oficial: Optional[float] = None
    criado_em: Optional[str] = None
    atualizado_em: Optional[str] = None

    @field_validator("tempo_inicial", "tempo_final", "criado_em", "atualizado_em", mode="before")
    def dt_to_str(cls, v):
        if isinstance(v, (str, type(None))):
            return v
        return v.isoformat()

    class Config:
        from_attributes = True