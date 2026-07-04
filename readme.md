
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
sudo apt install python3-rpi.gpio python3-pigpio
```

O caminho recomendado para PWM por hardware e o PWM do kernel via
`/sys/class/pwm`, habilitado com `dtoverlay=pwm-2chan`. Para configurar:

```bash
chmod +x rasp_scripts/configurar_pwm_hardware.sh
./rasp_scripts/configurar_pwm_hardware.sh
sudo reboot
```

Depois do reboot, `AGILITY_IR_PWM_BACKEND=auto` tenta `kernel_pwm` primeiro,
depois `pigpio.hardware_PWM` e por ultimo `RPi.GPIO.PWM`. Use
`AGILITY_IR_PWM_BACKEND=kernel_pwm` para exigir PWM por hardware do kernel.
Em algumas imagens Debian/Raspberry Pi OS, o APT fornece `python3-pigpio`, mas
nao fornece o daemon `pigpiod`; nesse caso o backend nao depende mais dele para
PWM por hardware.

Configuração recomendada para o circuito IR. Ajuste `AGILITY_IR_FREQUENCY` com o resultado de `rasp_scripts/testar_sensor_ir.py`:

```bash
export AGILITY_IR_FREQUENCY=52000
export AGILITY_IR_PWM_BACKEND=auto
export AGILITY_IR_PWM_CHIP=0
export AGILITY_IR_PWM_CHANNEL=0
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.002
export AGILITY_IR_BURST_OFF=0.018
export AGILITY_SENSOR_READ_MODE=auto
export AGILITY_SENSOR_POLL_INTERVAL=0.001
export AGILITY_SENSOR_ACTIVE_LEVEL=LOW
export AGILITY_SENSOR_REARM_STABLE=0.02
export AGILITY_SENSOR_TRIGGER_CONFIRM=0.002
export AGILITY_SENSOR_SIGNAL_TIMEOUT=0.12
export AGILITY_SENSOR_READY_CONFIRM=0.05
export AGILITY_SENSOR_READY_MIN_RATIO=0.2
export AGILITY_SENSOR_REQUIRE_READY=1
```

Para calibrar automaticamente no startup e salvar a recomendação:

```bash
export AGILITY_IR_CALIBRATE_ON_STARTUP=1
export AGILITY_IR_CALIBRATION_APPLY=1
export AGILITY_IR_CALIBRATION_SAVE=1
export AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY=52000
export AGILITY_IR_CALIBRATION_PREFERENCE_TOLERANCE=1.0
```

`AGILITY_SENSOR_READ_MODE=auto` tenta interrupção por borda quando a portadora não está em rajadas. Com `AGILITY_IR_BURST_ENABLED=1`, o backend usa polling lógico rápido para que as rajadas não gerem falsos eventos.
No circuito com LED indicador, o LED costuma ficar ligado sem sinal e apagar quando o receptor detecta IR. Nessa montagem, o feixe alinhado normalmente deixa o GPIO em `HIGH`, e o feixe quebrado/sem sinal deixa em `LOW`. Por isso `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`.
Como esse tipo de receptor pode bloquear portadora continua, o emissor usa rajadas com baixa ocupacao de envelope: 2 ms ligado e 18 ms desligado. O backend considera o feixe alinhado enquanto enxerga pulsos recentes, controlado por `AGILITY_SENSOR_SIGNAL_TIMEOUT`.
Na autorização da largada, o backend amostra o GPIO por `AGILITY_SENSOR_READY_CONFIRM` segundos e aceita se pelo menos `AGILITY_SENSOR_READY_MIN_RATIO` das leituras indicarem feixe alinhado.

Para calibrar, pare o backend na Raspberry e rode:

```bash
python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm
```

O script varre de 10 kHz a 60 kHz, deixa o emissor desligado por 1 segundo entre emissões, mantém a portadora contínua por 1 segundo nas frequências sensíveis e imprime os `export AGILITY_*` recomendados. A mesma calibração também pode ser executada na tela `/config`.

Se o receptor estiver alimentado em 5V como no diagrama do TCC, confirme que o sinal no GPIO17 não passa de 3.3V antes de ligar diretamente na Raspberry Pi.

- Dê permissão de execução com

`chmod +x install_agility.sh`

# Execute na Raspberry Pi

`./install_agility.sh`
