
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

Na Raspberry Pi com GPIO, rode sem `--reload` para evitar inicialização duplicada do hardware:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
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

Configuração recomendada para o circuito IR. Ajuste `AGILITY_IR_FREQUENCY` com o resultado de `rasp_scripts/testar_sensor_ir.py`:

```bash
export AGILITY_IR_FREQUENCY=31000
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.002
export AGILITY_IR_BURST_OFF=0.002
export AGILITY_SENSOR_READ_MODE=auto
export AGILITY_SENSOR_POLL_INTERVAL=0.001
export AGILITY_SENSOR_ACTIVE_LEVEL=LOW
export AGILITY_SENSOR_REARM_STABLE=0.02
export AGILITY_SENSOR_TRIGGER_CONFIRM=0.002
export AGILITY_SENSOR_SIGNAL_TIMEOUT=0.03
export AGILITY_SENSOR_READY_CONFIRM=0.05
export AGILITY_SENSOR_READY_MIN_RATIO=0.2
export AGILITY_SENSOR_REQUIRE_READY=1
```

`AGILITY_SENSOR_READ_MODE=auto` tenta interrupção por borda quando a portadora não está em rajadas. Com `AGILITY_IR_BURST_ENABLED=1`, o backend usa polling lógico rápido para que as rajadas não gerem falsos eventos.
No circuito com LED indicador, o LED costuma ficar ligado sem sinal e apagar quando o receptor detecta IR. Nessa montagem, o feixe alinhado normalmente deixa o GPIO em `HIGH`, e o feixe quebrado/sem sinal deixa em `LOW`. Por isso `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`.
Como esse tipo de receptor pode bloquear portadora contínua, o emissor usa rajadas: 2 ms ligado e 2 ms desligado. O backend considera o feixe alinhado enquanto enxerga pulsos recentes, controlado por `AGILITY_SENSOR_SIGNAL_TIMEOUT`.
Na autorização da largada, o backend amostra o GPIO por `AGILITY_SENSOR_READY_CONFIRM` segundos e aceita se pelo menos `AGILITY_SENSOR_READY_MIN_RATIO` das leituras indicarem feixe alinhado.

Para calibrar, pare o backend na Raspberry e rode:

```bash
python rasp_scripts/testar_sensor_ir.py
```

O script varre de 10 kHz a 60 kHz, mantém a portadora contínua por 1 segundo nas frequências sensíveis e imprime os `export AGILITY_*` recomendados.

- Dê permissão de execução com

`chmod +x install_agility.sh`

# Execute na Raspberry Pi

`./install_agility.sh`
