
- Reference:
- <https://dorian599.medium.com/fastapi-getting-started-3294efe823a0>
- <https://medium.com/@habbema/construindo-apis-com-fastapi-e-sqlite-99af4cf3b444>

# Create a virtual environment named "fastapi-env"

python -m venv fastapi-env

# Activate the virtual environment

# On Windows

fastapi-env\Scripts\activate

<!-- venv\Scripts\activate -->

# On macOS and Linux

source fastapi-env/bin/activate

<!-- python3 -m venv venv source venv/bin/activate -->

# Install FastAPI and Uvicorn

pip install -r requirements.txt

# Run Your FastAPI Application

uvicorn app.main:app --reload

- Suba a API:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

# Rodar Comandos SQL terminal

sqlite3 agility.db

# Create/Recriate Tables

python create_tables.py

# Install Raspberry PI scripts

Na Raspberry Pi, instale primeiro o pacote de GPIO do sistema:

```bash
sudo apt update
sudo apt install python3-rpi.gpio
```

- Dê permissão de execução com

`chmod +x install_agility.sh`

# Execute na Raspberry Pi

`./install_agility.sh`
