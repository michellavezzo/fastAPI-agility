
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
- `pigpio` é opcional. Se o APT responder `Package 'pigpio' has no installation candidate`, ignore esse pacote: o código usa `RPi.GPIO.PWM` para o emissor IR quando `pigpio` não está disponível.

## Raspberry Pi: sensor IR e LED IR

A implementação segue a lógica do código Arduino: o receptor IR fica em entrada com pull-up e o LED IR é pulsado em 38 kHz.

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
export AGILITY_IR_FREQUENCY=31000
export AGILITY_IR_DUTY_CYCLE=50
export AGILITY_IR_EMITTER_ENABLED=1
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.002
export AGILITY_IR_BURST_OFF=0.002
export AGILITY_SENSOR_READ_MODE=auto
export AGILITY_SENSOR_POLL_INTERVAL=0.001
export AGILITY_SENSOR_DEBOUNCE=1.0
export AGILITY_SENSOR_REARM_STABLE=0.02
export AGILITY_SENSOR_REQUIRE_REARM=0
export AGILITY_SENSOR_REQUIRE_READY=1
export AGILITY_SENSOR_ACTIVE_LEVEL=LOW
export AGILITY_SENSOR_TRIGGER_CONFIRM=0.002
export AGILITY_SENSOR_SIGNAL_TIMEOUT=0.03
export AGILITY_SENSOR_READY_CONFIRM=0.05
export AGILITY_SENSOR_READY_MIN_RATIO=0.2
export AGILITY_SENSOR_IGNORED_LOG_INTERVAL=2.0
```

O modo padrão `AGILITY_SENSOR_READ_MODE=auto` tenta usar interrupção por borda com `GPIO.add_event_detect`, que é o caminho de menor latência no Raspberry Pi. Se a interrupção falhar, o backend volta automaticamente para polling rápido com `AGILITY_SENSOR_POLL_INTERVAL=0.001`. Para seguir exatamente o loop do tutorial, use `AGILITY_SENSOR_READ_MODE=polling`.

O teste do sensor atual mostrou resposta clara perto de `31000Hz`: com o emissor desligado o GPIO fica `LOW`, em `31000Hz` fica majoritariamente `HIGH`. Para esse sensor, o feixe alinhado é `HIGH` e o feixe quebrado/sem sinal é `LOW`; por isso o padrão do projeto passa a ser `AGILITY_IR_FREQUENCY=31000` e `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`.

Como esse tipo de receptor pode ignorar portadora contínua depois de alguns segundos, o emissor usa rajadas por padrão com `AGILITY_IR_BURST_ENABLED=1`, `AGILITY_IR_BURST_ON=0.002` e `AGILITY_IR_BURST_OFF=0.002`. O backend considera o feixe alinhado enquanto recebe pulsos recentes; se passar `AGILITY_SENSOR_SIGNAL_TIMEOUT=0.03` sem pulsos, considera feixe quebrado.

Para evitar duplo disparo quando o cachorro cruza o feixe, o backend ignora novas bordas durante `AGILITY_SENSOR_DEBOUNCE`. O bloqueio por rearme físico fica desligado por padrão com `AGILITY_SENSOR_REQUIRE_REARM=0`, porque alguns receptores IR não voltam para o nível livre de forma estável. Use `AGILITY_SENSOR_REQUIRE_REARM=1` apenas se `GET /hardware/estado` mostrar `sensor_estado_sinal` alternando de forma limpa entre `feixe_alinhado` e `feixe_quebrado`. `AGILITY_SENSOR_TRIGGER_CONFIRM=0.002` confirma que o nível ativo permaneceu estável por 2 ms antes de aceitar a largada/chegada.

Antes de autorizar a largada, o backend exige feixe alinhado com `AGILITY_SENSOR_REQUIRE_READY=1`, assim como o tutorial só inicia o cronômetro quando o feixe está detectado. Para validar o sinal, observe `sensor_nivel_atual`, `sensor_estado_sinal`, `sensor_transicoes` e `sensor_ultima_transicao` em `GET /hardware/estado` com o feixe livre e depois bloqueado.

Para evitar bloqueio por uma queda curta do receptor, a autorização não usa apenas a última leitura instantânea. Ela amostra o GPIO por `AGILITY_SENSOR_READY_CONFIRM=0.05` e aceita a largada quando pelo menos `AGILITY_SENSOR_READY_MIN_RATIO=0.2` das leituras indicam feixe alinhado. O valor é menor porque, em modo rajada, o nível alterna entre sinal e intervalo. O resultado da última amostragem aparece em `sensor_ultima_amostra_pronto`.

Para sensor desconhecido, pare o backend e rode:

```bash
python rasp_scripts/testar_sensor_ir.py
```

O script mede o GPIO com o emissor desligado e depois varre frequências de 30 kHz a 60 kHz. Use a frequência que produzir diferença clara em relação ao emissor desligado; se a resposta for `HIGH` com feixe alinhado, configure `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`.

Nunca conecte 5V direto no GPIO17 ou no GPIO18.
