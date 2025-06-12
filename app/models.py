# app/models.py

from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from .database import Base
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    competicoes = relationship("Competicao", back_populates="responsavel")
    avaliacoes = relationship("Avaliacao", back_populates="prova")

class Competicao(Base):
    __tablename__ = "competicoes"

    id_competicao = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    data = Column(Date, nullable=False)
    localizacao = Column(String, nullable=False)
    nomes_arbitros_convidados = Column(String)
    nome_diretor_evento = Column(String)
    nome_responsavel_secretaria = Column(String)
    nome_veterinario = Column(String)
    responsavel_id = Column(Integer, ForeignKey("users.id"))
    responsavel = relationship("User", back_populates="competicoes")

class Prova(Base):
    __tablename__ = "prova"

    id_prova = Column(Integer, primary_key=True, index=True)
    id_competicao = Column(Integer, ForeignKey("competicoes.id_competicao"))
    categoria = Column(String, nullable=False)
    classe = Column(String, nullable=False)
    num_obstaculos = Column(Integer, nullable=False)
    tsp = Column(Integer, nullable=False)
    tmp = Column(Integer, nullable=False)
    vel_media_necessaria = Column(Float, nullable=False)
    comprimento_pista = Column(Integer, nullable=False)
    avaliacoes = relationship("Avaliacao", back_populates="prova")
    descricao = Column(String, nullable=True)
    inscricoes = relationship("Inscricao", back_populates="prova")
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), onupdate=func.now())

class Inscricao(Base):
    __tablename__ = "inscricao"

    id_inscricao = Column(Integer, primary_key=True, index=True)
    id_prova = Column(Integer, ForeignKey("prova.id_prova"))
    id_competidor = Column(Integer, ForeignKey("competidor.id_competidor"))
    microchip_cao = Column(String, ForeignKey("cao.microchip"), nullable=False)
    colete_competidor = Column(String, nullable=False)
    tempo_prova = Column(Float, nullable=True)
    faltas_prova = Column(Integer, nullable=True)
    recusas_prova = Column(Integer, nullable=True)
    vel_media = Column(Float, nullable=True)
    pontuacao = Column(Integer, nullable=True)
    hora_inicio = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="pendente")
    cronometros = relationship("Cronometragem", back_populates="inscricao")
    resultados = relationship("Resultado", back_populates="inscricao")
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), onupdate=func.now())

    prova = relationship("Prova", back_populates="inscricoes")
    competidor = relationship("Competidor", back_populates="inscricoes")
    cao = relationship("Cao", back_populates="inscricoes")

class Cronometragem(Base):
    __tablename__ = "cronometro"

    id_cronometro = Column(Integer, primary_key=True, index=True)
    id_inscricao = Column(Integer, ForeignKey("inscricao.id_inscricao"))
    tempo_inicial = Column(DateTime(timezone=True), nullable=False)
    tempo_final = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="parado")
    tempo_oficial = Column(Float, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), onupdate=func.now())

    inscricao = relationship("Inscricao", back_populates="cronometros")

class Avaliacao(Base):
    __tablename__ = "avaliacao"

    id_avaliacao = Column(Integer, primary_key=True, index=True)
    id_prova = Column(Integer, ForeignKey("prova.id_prova"))
    id_juiz = Column(Integer, ForeignKey("juiz.id_juiz"))
    diretor_prova = Column(String, nullable=False)
    comentarios = Column(String, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), onupdate=func.now())

    prova = relationship("Prova", back_populates="avaliacoes")
    juiz = relationship("Juiz", back_populates="avaliacoes")

class Competidor(Base):
    __tablename__ = "competidor"

    id_competidor = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    escola = Column(String, nullable=False)
    # microchip = Column(String, unique=True, nullable=False)
    # raca = Column(String, nullable=False)
    # idade = Column(Integer, nullable=False)
    # sexo = Column(String, nullable=False)
    inscricoes = relationship("Inscricao", back_populates="competidor")

class Cao(Base):
    __tablename__ = "cao"

    microchip = Column(String, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    raca = Column(String, nullable=False)
    cernelha = Column(String, nullable=False)
    categoria_salto = Column(String, nullable=False)
    is_cao_branco = Column (Boolean, nullable=False, default=False)
    inscricoes = relationship("Inscricao", back_populates="cao")
    # idade = Column(Integer, nullable=False)
    # sexo = Column(String, nullable=False)

class Juiz(Base):
    __tablename__ = "juiz"

    id_juiz = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    avaliacoes = relationship("Avaliacao", back_populates="juiz")

class Resultado(Base):
    __tablename__ = "resultado"

    id_resultado = Column(Integer, primary_key=True, index=True)
    id_inscricao = Column(Integer, ForeignKey("inscricao.id_inscricao"))
    posicao = Column(Integer, nullable=False)
    total_pontos_t = Column(Integer, nullable=False)
    total_pontos_tp = Column(Integer, nullable=False)
    inscricao = relationship("Inscricao", back_populates="resultado")