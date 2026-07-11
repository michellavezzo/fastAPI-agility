
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
export AGILITY_IR_FREQUENCY=50000
export AGILITY_IR_DUTY_CYCLE=50
export AGILITY_IR_PWM_BACKEND=auto
export AGILITY_IR_PWM_CHIP=0
export AGILITY_IR_PWM_CHANNEL=0
export AGILITY_IR_EMITTER_ENABLED=1
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.006
export AGILITY_IR_BURST_OFF=0.014
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
export AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY=50000
export AGILITY_IR_CALIBRATION_PREFERENCE_TOLERANCE=1.0
```

Quando `AGILITY_IR_CALIBRATE_ON_STARTUP=1`, o backend bloqueia o startup até
terminar a tentativa. A calibração também pode ser iniciada pela tela `/config`
ou pelo endpoint `POST /config/ir/calibracao`.

O fluxo mede primeiro todas as janelas temporais `noise_scan`, com o emissor
continuamente desligado. As janelas não representam frequências OFF: cada uma é
pareada por posição com a janela posterior de `active_scan` da candidata
correspondente. Depois de identificar o nível de sinal na leitura ativa, a
calibração rejeita a candidata quando esse nível já apareceu continuamente por
pelo menos `2 ms` na janela OFF (`noise_detected_off`) ou quando o contraste
ativo menos OFF fica abaixo de `25` pontos percentuais
(`insufficient_contrast`). O piso de `2 ms` e o contraste mínimo não podem ser
reduzidos por configuração.

Após o teste de portadora contínua, no máximo cinco finalistas seguem para os
testes operacionais. O teste de margem mede `100%`, `70%`, `40%` e `20%` do duty
solicitado e registra o menor valor aprovado em `minimum_stable_duty`; esse
valor é um indicador prático da margem óptica/elétrica, enquanto a recomendação
continua operando no duty solicitado. Quando várias candidatas ficam próximas,
`AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY` participa do desempate depois das
métricas operacionais.

O teste de rajada restaura o duty solicitado e usa o envelope operacional de
`6 ms` ligado e `14 ms` desligado. Com `period = burst_on + burst_off`, o timeout
recomendado é `max(3 * period, 2 * max_gap + 5 ms)`, onde `max_gap` é o maior
intervalo sem pulso válido observado. O teto é `120 ms`; se o cálculo ultrapassa
esse valor, a candidata é rejeitada com `signal_gap_too_large`, sem truncar o
timeout para forçar sua aprovação.

Em seguida, o emissor é desligado para testar a liberação e religado em rajadas
para medir a reaquisição. Essa quebra é uma simulação elétrica, não uma passagem
física: `diagnostics.physical_break_validated` e
`recommendation.physical_break_validated` permanecem `false`.

O resultado inclui os campos `noise_scan`, `rejected`, `margin`, `burst`,
`break_tests` e `diagnostics`. Em `GET /config/ir/status`,
`calibration.last_attempt` guarda a tentativa concluída mesmo quando `ok=false`;
somente uma tentativa válida atualiza `calibration.last_result`,
`saved_calibration`, a configuração em runtime e `ir_calibration.json`. Assim,
uma tentativa inválida preserva a calibração válida anterior.

O modo padrão `AGILITY_SENSOR_READ_MODE=auto` tenta usar interrupção por borda quando a portadora não está em rajadas. Com `AGILITY_IR_BURST_ENABLED=1`, o backend usa polling lógico rápido para evitar que cada pulso da rajada seja tratado como largada/chegada. O evento de prova é disparado apenas quando o backend deixa de receber pulsos recentes por `AGILITY_SENSOR_SIGNAL_TIMEOUT`.

O circuito com LED indicador costuma ficar aceso sem sinal e apagar quando o receptor detecta IR. Nessa montagem, o GPIO normalmente fica `HIGH` com feixe alinhado e `LOW` com feixe quebrado/sem sinal; por isso `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`. Confirme sempre com o script de teste, porque a ligação elétrica pode inverter esse comportamento.

Como esse tipo de receptor pode ignorar portadora contínua depois de algum tempo, o emissor usa rajadas por padrão com `AGILITY_IR_BURST_ENABLED=1`, `AGILITY_IR_BURST_ON=0.006` e `AGILITY_IR_BURST_OFF=0.014`. O backend considera o feixe alinhado enquanto recebe pulsos recentes. `AGILITY_SENSOR_SIGNAL_TIMEOUT=0.12` é o default e o teto de segurança; uma calibração válida pode recomendar um valor menor pela fórmula dinâmica descrita acima.

Para evitar duplo disparo quando o cachorro cruza o feixe, o backend ignora novas bordas durante `AGILITY_SENSOR_DEBOUNCE`. O bloqueio por rearme físico fica desligado por padrão com `AGILITY_SENSOR_REQUIRE_REARM=0`, porque alguns receptores IR não voltam para o nível livre de forma estável. Use `AGILITY_SENSOR_REQUIRE_REARM=1` apenas se `GET /hardware/estado` mostrar `sensor_estado_sinal` alternando de forma limpa entre `feixe_alinhado` e `feixe_quebrado`. `AGILITY_SENSOR_TRIGGER_CONFIRM=0.002` confirma que o nível ativo permaneceu estável por 2 ms antes de aceitar a largada/chegada.

Antes de autorizar a largada, o backend exige feixe alinhado com `AGILITY_SENSOR_REQUIRE_READY=1`, assim como o tutorial só inicia o cronômetro quando o feixe está detectado. `sensor_nivel_atual`, `sensor_estado_sinal`, `sensor_transicoes` e `sensor_ultima_transicao` são diagnósticos do sinal bruto; em modo rajada, use `sensor_estado_feixe` e `sensor_feixe_logico_alinhado` como estado lógico da barreira.

Para evitar bloqueio por uma queda curta do receptor, a autorização não usa apenas a última leitura instantânea. Ela amostra o GPIO por `AGILITY_SENSOR_READY_CONFIRM=0.05` e aceita a largada quando pelo menos `AGILITY_SENSOR_READY_MIN_RATIO=0.2` das leituras indicam feixe alinhado. O valor é menor porque, em modo rajada, o nível alterna entre sinal e intervalo. O resultado da última amostragem aparece em `sensor_ultima_amostra_pronto`.

Para sensor desconhecido, pare o backend e rode:

```bash
python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm
```

O script executa o mesmo fluxo do backend, incluindo janelas OFF pareadas,
rejeições por ruído/contraste, margem de duty, rajada operacional e quebra
elétrica simulada. Ao final, imprime os `export AGILITY_*` recomendados. Use
esses valores depois de reiniciar o serviço, mas não trate o teste automático
como validação física.

Validação física pendente na Raspberry:

1. Com o caminho livre, consulte `GET /hardware/estado`, confirme
   `sensor_estado_feixe=feixe_alinhado` e
   `sensor_feixe_logico_alinhado=true`, e registre o valor de
   `sensor_quebras_logicas`.
2. Cubra integralmente o caminho entre emissor e receptor com um objeto opaco e
   mantenha-o por pelo menos `1 s`, acima do teto de timeout de `120 ms`.
3. Confirme `sensor_estado_feixe=feixe_quebrado`,
   `sensor_feixe_logico_alinhado=false` e o incremento de exatamente uma unidade
   em `sensor_quebras_logicas`.
4. Retire o objeto e confirme a reaquisição com
   `sensor_estado_feixe=feixe_alinhado` e
   `sensor_feixe_logico_alinhado=true`; depois repita com uma passagem na
   velocidade e nas distâncias reais da prova.

Frequências menores podem manter o feixe alinhado e, ainda assim, tornar a
passagem mais difícil de detectar quando a margem óptica ou os reflexos são
altos demais. Frequência isolada não basta para selecionar a configuração;
`minimum_stable_duty` é um indicador prático da margem, mas não substitui a
validação física. Um triângulo automotivo de emergência não tem caracterização
IR conhecida neste projeto e não deve ser o único objeto de teste.

Nunca conecte 5V direto no GPIO17 ou no GPIO18. Se o receptor estiver alimentado em 5V como no diagrama do TCC, confirme com multímetro/osciloscópio que o OUT recebido pelo GPIO17 não passa de 3.3V; se passar, use divisor resistivo, level shifter ou saída open-collector com pull-up em 3.3V.
