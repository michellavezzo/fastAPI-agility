
# Agility Back

- Reference:
- <https://dorian599.medium.com/fastapi-getting-started-3294efe823a0>
- <https://medium.com/@habbema/construindo-apis-com-fastapi-e-sqlite-99af4cf3b444>

## Execução local do backend

Crie o ambiente virtual:

```bash
python3 -m venv venv
```

Ative o ambiente virtual:

No Windows:

```powershell
venv\Scripts\activate
```

No macOS e Linux:

```bash
source venv/bin/activate
```

Instale as dependências do backend:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Crie ou atualize as tabelas:

```bash
python create_tables.py
```

Suba a API:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Abrir o banco SQLite no terminal:

```bash
sqlite3 agility.db
```

## Script de instalação rápida do backend

Se quiser instalar apenas o backend neste repositório:

```bash
chmod +x install_agility.sh
./install_agility.sh
```

Esse script cria ou reaproveita `venv` ou `fastapi-env` e instala os pacotes dentro do ambiente virtual correto.

## Raspberry Pi: backend + frontend automáticos

Os scripts da pasta `rasp_scripts` foram feitos para ficar juntos em uma pasta dedicada na Raspberry Pi e, a partir dali, clonar backend e frontend no mesmo diretório.

Passo a passo:

```bash
mkdir -p ~/Desktop/agility
cd ~/Desktop/agility
cp -R /caminho/do/projeto/rasp_scripts/. .
chmod +x *.sh
./menu_agility.sh
```

Cenários mais comuns:

1. Primeira instalação: execute `clonar_projetos.sh`, depois `instalar_dependencias.sh`, e por fim `executar_projeto.sh`.
2. Projeto já clonado, mas sem dependências: execute `instalar_dependencias.sh`.
3. Projeto já instalado: execute `executar_projeto.sh`.
4. Ambiente antigo com `fastapi-env`: os scripts detectam esse nome automaticamente, mas novos ambientes são criados como `venv`.

Observações:

- O backend sempre deve ser iniciado com o Python do ambiente virtual, nunca com `/usr/bin/python`.
- Na Raspberry Pi com GPIO, rode o backend sem `--reload`; o reload cria processo supervisor e pode inicializar hardware mais de uma vez durante desenvolvimento.
- Se aparecer `No module named sqlalchemy` ou `No module named uvicorn`, rode novamente `rasp_scripts/instalar_dependencias.sh`.
- Na Raspberry Pi, o script tenta instalar `RPi.GPIO`; se isso falhar, o backend ainda pode rodar em modo sem GPIO.
- Para PWM por hardware no emissor IR, o caminho recomendado é o PWM do kernel via `/sys/class/pwm`, habilitado com `dtoverlay=pwm-2chan`. Rode `rasp_scripts/configurar_pwm_hardware.sh` na Raspberry e reinicie. Com `GPIO18`, o overlay padrão usa `pwmchip0/pwm0`.
- Com `AGILITY_IR_PWM_BACKEND=kernel_pwm`, falha do overlay/permissão deixa o emissor em erro explícito. Com `AGILITY_IR_PWM_BACKEND=auto`, o backend tenta `kernel_pwm`, depois `pigpio.hardware_PWM` e por último `RPi.GPIO.PWM`.

## Raspberry Pi: sensor IR e LED IR

A implementação segue a lógica de receptores IR demodulados: o receptor fica em entrada com pull-up e o LED IR emite uma portadora PWM em rajadas curtas. A frequência exata depende do sensor real e deve ser calibrada com `rasp_scripts/testar_sensor_ir.py`.

Pinos padrão em modo BCM:

| Função | GPIO BCM | Pino físico |
| --- | --- | --- |
| Receptor IR OUT | GPIO17 | 11 |
| LED IR PWM | GPIO18 | 12 |
| 3.3V do receptor | - | 1 |
| 5V do LED IR | - | 2 ou 4 |
| GND comum | - | 6 |

Ligação do receptor IR:

```text
Raspberry 3.3V pino 1  -> VCC do receptor IR
Raspberry GND  pino 6  -> GND do receptor IR
Raspberry GPIO17 pino 11 -> OUT do receptor IR
```

Ligação do LED IR com transistor NPN:

```text
Raspberry 5V pino 2 ou 4 -> resistor 180Ω/220Ω -> anodo LED IR
catodo LED IR -> coletor do transistor NPN
emissor do transistor NPN -> GND comum
Raspberry GPIO18 pino 12 -> resistor 1kΩ -> base do transistor NPN
```

Variáveis para trocar os pinos ou desabilitar o emissor:

```bash
export AGILITY_GPIO_PIN=17
export AGILITY_IR_LED_PIN=18
export AGILITY_IR_FREQUENCY=56000
export AGILITY_IR_DUTY_CYCLE=50
export AGILITY_IR_PWM_BACKEND=auto
export AGILITY_IR_PWM_CHIP=0
export AGILITY_IR_PWM_CHANNEL=0
export AGILITY_IR_EMITTER_ENABLED=1
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.002
export AGILITY_IR_BURST_OFF=0.018
export AGILITY_SENSOR_READ_MODE=auto
export AGILITY_SENSOR_POLL_INTERVAL=0.001
export AGILITY_SENSOR_DEBOUNCE=1.0
export AGILITY_SENSOR_REARM_STABLE=0.02
export AGILITY_SENSOR_REQUIRE_REARM=0
export AGILITY_SENSOR_REQUIRE_READY=1
export AGILITY_SENSOR_ACTIVE_LEVEL=LOW
export AGILITY_SENSOR_TRIGGER_CONFIRM=0.002
export AGILITY_SENSOR_SIGNAL_TIMEOUT=0.12
export AGILITY_SENSOR_READY_CONFIRM=0.05
export AGILITY_SENSOR_READY_MIN_RATIO=0.2
export AGILITY_SENSOR_IGNORED_LOG_INTERVAL=2.0
```

Calibração automática opcional:

```bash
export AGILITY_IR_CALIBRATE_ON_STARTUP=1
export AGILITY_IR_CALIBRATION_APPLY=1
export AGILITY_IR_CALIBRATION_SAVE=1
export AGILITY_IR_USE_SAVED_CALIBRATION=1
```

Quando `AGILITY_IR_CALIBRATE_ON_STARTUP=1`, o backend bloqueia o startup até terminar a varredura. O resultado fica salvo em `ir_calibration.json` e também pode ser gerado pela tela `/config` ou pelo endpoint `POST /config/ir/calibracao`.

O modo padrão `AGILITY_SENSOR_READ_MODE=auto` tenta usar interrupção por borda quando a portadora não está em rajadas. Com `AGILITY_IR_BURST_ENABLED=1`, o backend usa polling lógico rápido para evitar que cada pulso da rajada seja tratado como largada/chegada. O evento de prova é disparado apenas quando o backend deixa de receber pulsos recentes por `AGILITY_SENSOR_SIGNAL_TIMEOUT`.

O circuito com LED indicador costuma ficar aceso sem sinal e apagar quando o receptor detecta IR. Nessa montagem, o GPIO normalmente fica `HIGH` com feixe alinhado e `LOW` com feixe quebrado/sem sinal; por isso `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`. Confirme sempre com o script de teste, porque a ligação elétrica pode inverter esse comportamento.

Como esse tipo de receptor pode ignorar portadora contínua depois de algum tempo, o emissor usa rajadas por padrão com `AGILITY_IR_BURST_ENABLED=1`, `AGILITY_IR_BURST_ON=0.002` e `AGILITY_IR_BURST_OFF=0.018`. O backend considera o feixe alinhado enquanto recebe pulsos recentes; se passar `AGILITY_SENSOR_SIGNAL_TIMEOUT=0.12` sem pulsos, considera feixe quebrado.

Para evitar duplo disparo quando o cachorro cruza o feixe, o backend ignora novas bordas durante `AGILITY_SENSOR_DEBOUNCE`. O bloqueio por rearme físico fica desligado por padrão com `AGILITY_SENSOR_REQUIRE_REARM=0`, porque alguns receptores IR não voltam para o nível livre de forma estável. Use `AGILITY_SENSOR_REQUIRE_REARM=1` apenas se `GET /hardware/estado` mostrar `sensor_estado_sinal` alternando de forma limpa entre `feixe_alinhado` e `feixe_quebrado`. `AGILITY_SENSOR_TRIGGER_CONFIRM=0.002` confirma que o nível ativo permaneceu estável por 2 ms antes de aceitar a largada/chegada.

Antes de autorizar a largada, o backend exige feixe alinhado com `AGILITY_SENSOR_REQUIRE_READY=1`, assim como o tutorial só inicia o cronômetro quando o feixe está detectado. Para validar o sinal, observe `sensor_nivel_atual`, `sensor_estado_sinal`, `sensor_transicoes` e `sensor_ultima_transicao` em `GET /hardware/estado` com o feixe livre e depois bloqueado.

Para evitar bloqueio por uma queda curta do receptor, a autorização não usa apenas a última leitura instantânea. Ela amostra o GPIO por `AGILITY_SENSOR_READY_CONFIRM=0.05` e aceita a largada quando pelo menos `AGILITY_SENSOR_READY_MIN_RATIO=0.2` das leituras indicam feixe alinhado. O valor é menor porque, em modo rajada, o nível alterna entre sinal e intervalo. O resultado da última amostragem aparece em `sensor_ultima_amostra_pronto`.

Para sensor desconhecido, pare o backend e rode:

```bash
python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm
```

O script mede o GPIO com o emissor desligado, varre frequências de 10 kHz a 60 kHz, deixa o emissor desligado por 1 segundo entre emissões e, nas frequências sensíveis, mantém a portadora contínua por 1 segundo para estimar se/quando o sensor satura. Ao final ele imprime os `export AGILITY_*` recomendados para o backend. Use esses valores depois de reiniciar o serviço.

Nunca conecte 5V direto no GPIO17 ou no GPIO18. Se o receptor estiver alimentado em 5V como no diagrama do TCC, confirme com multímetro/osciloscópio que o OUT recebido pelo GPIO17 não passa de 3.3V; se passar, use divisor resistivo, level shifter ou saída open-collector com pull-up em 3.3V.
