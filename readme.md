
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
export AGILITY_IR_FREQUENCY=50000
export AGILITY_IR_PWM_BACKEND=auto
export AGILITY_IR_PWM_CHIP=0
export AGILITY_IR_PWM_CHANNEL=0
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.006
export AGILITY_IR_BURST_OFF=0.014
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
export AGILITY_IR_USE_SAVED_CALIBRATION=1
export AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY=50000
export AGILITY_IR_CALIBRATION_PREFERENCE_TOLERANCE=1.0
```

A calibração lê primeiro todas as janelas temporais `noise_scan`, mantendo o
emissor continuamente desligado. Só depois executa as janelas ativas e pareia
cada uma, por posição, com a janela OFF correspondente. Uma candidata é
rejeitada se o nível que representaria sinal aparecer continuamente por pelo
menos `2 ms` com o emissor desligado (`noise_detected_off`) ou se o contraste
entre as janelas ativa e OFF for menor que `25` pontos percentuais
(`insufficient_contrast`).

A varredura ativa usa o mesmo envelope de `6 ms` ligado e `14 ms` desligado do
backend. A portadora contínua de `1 s` permanece apenas para medir quando o
receptor suprime o sinal; essa supressão não elimina uma candidata. No máximo
cinco finalistas passam pelo teste de margem, também em rajadas, a `100%`,
`70%`, `40%` e `20%` do duty solicitado. O menor duty aprovado aparece como
`minimum_stable_duty`, um indicador prático da margem óptica/elétrica; a
recomendação continua usando o duty solicitado. Duty mínimo mais baixo não é
automaticamente melhor, pois margem excessiva pode manter reflexos durante uma
passagem. O timeout é calculado como
`max(3 * period, 2 * max_gap + 5 ms)`. O limite é `120 ms`; candidatas que
exigirem timeout maior são rejeitadas.

O teste automático de quebra desliga o emissor e verifica liberação e
reaquisição, mas simula apenas uma interrupção elétrica. Ele não valida a
passagem física: `physical_break_validated` permanece `false`. O resultado
expõe `noise_scan`, `rejected`, `margin`, `burst`, `break_tests` e
`diagnostics`; `calibration.last_attempt` registra também uma tentativa
inválida. Somente um resultado válido substitui a calibração aplicada e o
`ir_calibration.json`, portanto uma falha preserva a última calibração válida.
Arquivos salvos sem timeout positivo ou com timeout acima de `120 ms` são
ignorados no startup.

`AGILITY_SENSOR_READ_MODE=auto` tenta interrupção por borda quando a portadora não está em rajadas. Com `AGILITY_IR_BURST_ENABLED=1`, o backend usa polling lógico rápido para que as rajadas não gerem falsos eventos.
No circuito com LED indicador, o LED costuma ficar ligado sem sinal e apagar quando o receptor detecta IR. Nessa montagem, o feixe alinhado normalmente deixa o GPIO em `HIGH`, e o feixe quebrado/sem sinal deixa em `LOW`. Por isso `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`.
Como esse tipo de receptor pode bloquear portadora continua, o emissor usa rajadas com ocupacao de envelope limitada: 6 ms ligado e 14 ms desligado. O backend considera o feixe alinhado enquanto enxerga pulsos recentes, controlado por `AGILITY_SENSOR_SIGNAL_TIMEOUT`.
Na autorização da largada, o backend amostra o GPIO por `AGILITY_SENSOR_READY_CONFIRM` segundos e aceita se pelo menos `AGILITY_SENSOR_READY_MIN_RATIO` das leituras indicarem feixe alinhado.

Para calibrar, pare o backend na Raspberry e rode:

```bash
python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm
```

O script usa o mesmo fluxo de calibração do backend e imprime os
`export AGILITY_*` recomendados. A mesma calibração também pode ser executada na
tela `/config`.

Depois da calibração, valide fisicamente na Raspberry com um objeto opaco que
cubra todo o caminho entre emissor e receptor. Com o caminho livre, confirme
`sensor_estado_feixe=feixe_alinhado` e
`sensor_feixe_logico_alinhado=true` em `GET /hardware/estado` e registre
`sensor_quebras_logicas`. Mantenha o objeto no feixe por pelo menos `1 s` e
confirme `sensor_estado_feixe=feixe_quebrado`,
`sensor_feixe_logico_alinhado=false` e o incremento de uma quebra lógica;
depois retire o objeto e confirme a reaquisição. `sensor_estado_sinal` é uma
amostra bruta e pode alternar nas janelas OFF da rajada, por isso não deve ser
usado sozinho para validar a passagem.

Frequências menores podem manter o feixe alinhado e ainda dificultar a passagem
quando a margem óptica ou os reflexos são altos demais. Frequência isolada não é
critério suficiente; `minimum_stable_duty` ajuda a comparar a margem, mas não
substitui o teste físico. Um triângulo automotivo de emergência não possui
caracterização IR conhecida neste projeto e não deve ser o único objeto de
validação.

Se o receptor estiver alimentado em 5V como no diagrama do TCC, confirme que o sinal no GPIO17 não passa de 3.3V antes de ligar diretamente na Raspberry Pi.

- Dê permissão de execução com

`chmod +x install_agility.sh`

# Execute na Raspberry Pi

`./install_agility.sh`
