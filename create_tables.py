# filepath: /Users/michellavezzo/Documents/UFPB/TCC/agility-back/app/models.py
from app.models import Base
from app.database import engine

Base.metadata.create_all(bind=engine)