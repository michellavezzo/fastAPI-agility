# Implementacao da barreira infravermelha

Data da consolidacao: 2026-07-03.

Este documento resume as decisoes, descobertas e referencias usadas na implementacao da barreira infravermelha do backend do TCC. O objetivo e servir como base para atualizar posteriormente o texto do TCC.

## Contexto do problema

O sistema precisa detectar a passagem de um objeto entre emissor IR e receptor IR, usando essa quebra do feixe para iniciar e finalizar a cronometragem da prova de agility.

Durante os testes foi observado que o receptor nao funcionava de forma confiavel quando o emissor ficava simplesmente ligado por muito tempo. O comportamento levantou a hipotese de saturacao ou supressao automatica do sinal pelo receptor. Como o sensor fisico utilizado nao possui identificacao, as documentacoes NEC, Sony, Vishay TSOP e Vishay TSSP foram tratadas como referencias de funcionamento, nao como garantia exata do modelo instalado.

## Hardware atual

- Raspberry Pi Zero 2 W.
- Receptor IR ligado ao `GPIO17` no modo BCM.
- Emissor IR ligado ao `GPIO18` no modo BCM.
- O circuito foi alterado para alimentacao em `3.3V`, evitando que o sinal de saida do receptor ultrapasse a tensao tolerada pelo GPIO da Raspberry Pi.
- O circuito de referencia do TCC indica que o LED indicador fica aceso sem sinal detectado e apaga quando ha sinal IR. Na leitura do GPIO, a interpretacao usada e:
  - feixe alinhado: nivel logico `HIGH`;
  - feixe quebrado ou sem sinal: nivel logico `LOW`;
  - portanto, `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`.

## Referencias tecnicas consultadas

- Raspberry Pi documentation, GPIO and 40-pin header: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio-and-the-40-pin-header
  - A documentacao informa que GPIOs de saida trabalham em `3.3V`, GPIOs de entrada sao tolerantes a `3.3V` e alerta para nao usar `5V` em componentes de `3.3V`.
  - A documentacao tambem lista PWM por hardware nos GPIOs `12`, `13`, `18` e `19`; por isso `GPIO18` e adequado para PWM por hardware.
- pigpio Python source/documentation: https://github.com/joan2937/pigpio/blob/master/pigpio.py
  - O modulo Python se comunica com o daemon `pigpiod`.
  - O uso normal exige o daemon ativo, normalmente iniciado com `sudo pigpiod` ou via `systemctl`.
  - `hardware_PWM(gpio, PWMfreq, PWMduty)` usa duty na escala `0..1000000`; duty de 50% corresponde a `500000`.
- rpi-hardware-pwm: https://github.com/Pioreactor/rpi_hardware_pwm
  - A biblioteca usa PWM por hardware via `/sys/class/pwm`.
  - A instalacao recomenda `dtoverlay=pwm-2chan` em `/boot/firmware/config.txt`.
  - Em modelos anteriores ao Raspberry Pi 5, o overlay padrao usa `GPIO18` como `PWM0` e `GPIO19` como `PWM1`.
- Vishay TSSP40 datasheet: https://www.vishay.com/docs/82458/tssp40.pdf
  - A familia TSSP e voltada a presenca, proximidade e barreira.
  - O datasheet descreve saida ativa em nivel baixo em resposta a rajadas IR.
  - A frequencia da rajada deve corresponder ao centro de portadora do componente.
  - O documento cita uso tipico como sensor reflexivo ou de quebra de feixe.
- Vishay TSOP382/TSOP384 datasheet: https://www.vishay.com/docs/82491/tsop382.pdf
  - Sensores TSOP usam AGC e filtros para suprimir disturbios.
  - O datasheet explicita que sinais continuos podem ser suprimidos.
  - Isso reforca a escolha de rajadas continuas, em vez de portadora 100% continua.
- Documentos anexados inicialmente:
  - `Infra_Red_NEC.pdf`
  - `Sony_Protocol_2.pdf`
  - Foram usados como indicacao de que receptores IR demodulados esperam portadora em rajadas, com pausas, e nao necessariamente luz IR continua.

## Testes executados e aprendizado

O script `rasp_scripts/testar_sensor_ir.py` foi ajustado para iniciar a varredura em `10000Hz` e desligar o emissor por `1s` entre emissoes. Essa pausa existe para permitir que o receptor saia de uma possivel zona de saturacao ou supressao.

Resultado importante observado na Raspberry:

- O sensor respondeu em ampla faixa de frequencias entre `10kHz` e `60kHz`.
- Isso nao significa que o sensor seja igualmente sensivel a todas elas; o conjunto real `RPi.GPIO.PWM + LED IR + receptor + circuito` pode produzir respostas amplas.
- O resultado de maior interesse apareceu na faixa de `50kHz` a `60kHz`.
- O teste de portadora continua por `1s` indicou frequencias estaveis, sem perda do nivel de sinal, em torno de:
  - `57000Hz`
  - `59000Hz`
  - `58000Hz`
  - `56000Hz`
  - `53000Hz`
- A recomendacao inicial do teste apontou `57000Hz`, mas os testes finais com `kernel_pwm` e circuito estabilizado em `3.3V` indicaram `50000Hz` como valor pratico melhor neste conjunto.

Resultado apos ajustes de circuito e nova calibracao:

- Calibracao completa via `POST /config/ir/calibracao`:
  - `preferred_frequency=50000`
  - `preference_tolerance=1.0`
  - frequencia recomendada: `50000Hz`
  - `scan_signal_pct=100.0`
  - `scan_delta=98.754`
  - saturacao no hold de `1s`: `false`
  - `hold_pct=99.89`
  - `lost_after=null`
- Teste direto com backend parado em `50000Hz`:
  - baseline com emissor desligado: `3.692%`
  - leitura com emissor em `50000Hz`: `100.0%`
  - `scan_delta=96.308`
  - saturacao no hold de `1s`: `false`
  - `hold_pct=99.784`
- Teste fisico com backend rodando em rajadas `2ms/18ms`:
  - 117 amostras observadas
  - 117/117 ficaram com `sensor_estado_feixe=feixe_alinhado`
  - `max_gap=0.018s`
  - `logical_break_delta=0`
  - `break_seen=false`
  - Interpretacao: se o emissor/receptor foi tampado durante essa janela, o bloqueio fisico nao cortou todo o IR recebido; o backend continuou recebendo pulsos dentro do timeout logico.
- Teste de envelopes de rajada em `50000Hz`, `50%` de duty da portadora, duracao de `8s` por padrao:
  - `2ms/18ms`: `high_pct=12.5`, `max_gap=0.021s`, sem perda.
  - `4ms/16ms`: `high_pct=21.4`, `max_gap=0.020s`, sem perda.
  - `6ms/14ms`: `high_pct=29.6`, `max_gap=0.018s`, sem perda.
  - `8ms/12ms`: perdeu sinal apos `4.024s`.
  - `10ms/10ms`: perdeu sinal apos `2.783s`.
  - `12ms/8ms`: perdeu sinal apos `2.240s`.
  - `14ms/6ms`: perdeu sinal apos `1.853s`.
  - `16ms/4ms`: perdeu sinal apos `1.586s`.
  - `18ms/2ms`: perdeu sinal apos `1.384s`.
- Teste de portadora continua em `50000Hz`, `50%` de duty, por `60s`:
  - perdeu sinal apos `1.314s`;
  - resultado final `high_pct=2.0`, `low_pct=98.0`, `max_gap=58.806s`.
- Teste longo de `6ms/14ms` por `60s`:
  - `high_pct=30.1`, `low_pct=69.9`, `max_gap=0.020s`, `lost_after=null`.
  - Este foi o melhor envelope encontrado antes de iniciar saturacao/supressao.

## Decisao de arquitetura

A solucao adotada foi uma barreira por rajadas continuas:

- Durante o periodo "ligado", o emissor gera uma portadora PWM rapida, por exemplo `56kHz`.
- Durante o periodo "desligado", o emissor pausa por poucos milissegundos.
- O backend interpreta o feixe como alinhado enquanto recebe pulsos recentes do receptor.
- Se o backend deixa de receber pulsos por `AGILITY_SENSOR_SIGNAL_TIMEOUT`, considera que o feixe foi quebrado.

Configuracao padrao implementada:

```bash
export AGILITY_GPIO_PIN=17
export AGILITY_IR_LED_PIN=18
export AGILITY_IR_FREQUENCY=50000
export AGILITY_IR_DUTY_CYCLE=50
export AGILITY_IR_PWM_BACKEND=auto
export AGILITY_IR_BURST_ENABLED=1
export AGILITY_IR_BURST_ON=0.006
export AGILITY_IR_BURST_OFF=0.014
export AGILITY_SENSOR_ACTIVE_LEVEL=LOW
export AGILITY_SENSOR_SIGNAL_TIMEOUT=0.12
export AGILITY_SENSOR_TRIGGER_CONFIRM=0.002
export AGILITY_SENSOR_READY_MIN_RATIO=0.2
export AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY=50000
export AGILITY_IR_CALIBRATION_PREFERENCE_TOLERANCE=1.0
```

Justificativa do envelope padrao:

- No teste real da Raspberry com o circuito alimentado em `3.3V`, o sensor ainda detectou o emissor; portanto a montagem atual e suficiente para testes.
- A portadora continua gerou leitura inicial, mas perdeu estabilidade rapidamente; no teste final com `kernel_pwm`, `50000Hz` respondeu com melhor margem e sem saturacao no hold de `1s`.
- A rajada `2ms/2ms` tambem funcionou no inicio, mas saturou em aproximadamente `3s` no teste de 5s.
- A rajada `2ms/18ms` manteve pulsos ate o final do teste de 5s, mas deixava o GPIO em nivel de sinal por tempo muito baixo para o LED indicador bruto parecer apagado.
- O teste comparativo de envelopes mostrou que `6ms/14ms` e o maior tempo ligado que permaneceu estavel por `60s`; a partir de `8ms/12ms`, o receptor comeca a suprimir o sinal depois de alguns segundos.
- Por isso o default passou a usar `AGILITY_IR_BURST_ON=0.006`, `AGILITY_IR_BURST_OFF=0.014` e `AGILITY_SENSOR_SIGNAL_TIMEOUT=0.12`, permitindo tolerar pequenas perdas de pulso sem tratar cada pausa da rajada como quebra do feixe. O timeout foi mantido acima da lacuna ideal de `20ms` porque o backend real pode ter jitter de polling quando esta atendendo API/WebSocket.
- O LED indicador do circuito do TCC esta ligado ao sinal bruto do receptor. Em modo rajada, esse LED nao representa o `sensor_estado_feixe` logico do backend: ele pode continuar visualmente aceso ou parcialmente aceso mesmo com o feixe alinhado, porque o receptor alterna entre pulsos detectados e pausas. Para um LED que apague com feixe logico alinhado e acenda ao cortar o feixe, e necessario adicionar uma etapa de retencao/monostavel/RC no circuito ou acionar esse LED por um GPIO controlado pelo backend.
- Como o receptor nao identificado respondeu a quase toda a faixa testada, pequenas diferencas de amostragem nao devem definir a frequencia final. `AGILITY_IR_CALIBRATION_PREFERENCE_TOLERANCE=1.0` trata respostas dentro de 1 ponto percentual como empate e usa `AGILITY_IR_CALIBRATION_PREFERRED_FREQUENCY=50000` como desempate.

Observacao eletrica:

- Manter o receptor IR alimentado em `3.3V` protege o GPIO da Raspberry porque a saida do receptor tambem fica em nivel seguro.
- Se for necessario mais alcance ou intensidade no LED IR, a alternativa mais segura e alimentar apenas o ramo emissor do LED IR em `5V`, mantendo GND comum e acionamento via transistor pelo GPIO.
- Se o receptor for alimentado em `5V`, a saida para o GPIO17 deve passar por divisor de tensao ou level shifter, pois os GPIOs da Raspberry nao sao tolerantes a `5V`.

## Uso de PWM por hardware

O caminho recomendado passou a ser o PWM de hardware exposto pelo kernel em
`/sys/class/pwm`, porque ele nao depende do daemon `pigpiod`. A referencia
principal para esse fluxo e a biblioteca `rpi-hardware-pwm`, que documenta
`dtoverlay=pwm-2chan` em `/boot/firmware/config.txt`; em modelos anteriores ao
Pi 5, esse overlay usa `GPIO18` como `PWM0` e `GPIO19` como `PWM1`.

Configuracao na Raspberry:

```bash
chmod +x rasp_scripts/configurar_pwm_hardware.sh
./rasp_scripts/configurar_pwm_hardware.sh
sudo reboot
```

Depois do reboot:

```bash
lsmod | grep pwm
ls -la /sys/class/pwm
python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm --freqs 50000 --duration 0.2 --skip-hold
```

Variaveis para exigir esse caminho no backend:

```bash
export AGILITY_IR_PWM_BACKEND=kernel_pwm
export AGILITY_IR_PWM_CHIP=0
export AGILITY_IR_PWM_CHANNEL=0
```

Em `auto`, a ordem agora e:

- `kernel_pwm`: PWM do kernel via `/sys/class/pwm`.
- `pigpio`: `pigpio.hardware_PWM`, quando `pigpiod` existir.
- `rpi_gpio`: fallback por software com `RPi.GPIO.PWM`.

## Uso legado de pigpio.hardware_PWM

Foi implementado suporte a `pigpio.hardware_PWM` porque `RPi.GPIO.PWM` e PWM por software e pode variar mais em frequencias altas. A Raspberry Pi oferece PWM por hardware em GPIOs especificos; no projeto, `GPIO18` e um desses pinos.

Instalacao minima na Raspberry:

```bash
sudo apt update
sudo apt install python3-pigpio python3-rpi.gpio
```

Para usar `pigpio.hardware_PWM`, alem do modulo Python e necessario que o
daemon `pigpiod` esteja instalado e ativo. Na Raspberry testada neste chat, o
pacote `python3-pigpio` estava disponivel, mas o pacote servidor `pigpio` nao
tinha candidato no APT. O pacote `pigpio-tools` informou que, nessa base Debian,
apenas o lado cliente e empacotado porque o servidor e incompativel com os
kernels Debian. Resultado pratico: o modulo `pigpio` importa, mas
`pigpio.pi().connected` retorna `False`. Por isso foi adicionado o backend
`kernel_pwm`, que evita depender de `pigpiod`.

Variavel de selecao:

```bash
export AGILITY_IR_PWM_BACKEND=auto
```

Modos:

- `auto`: tenta `kernel_pwm`; se falhar, tenta `pigpio.hardware_PWM`; se falhar, usa `RPi.GPIO.PWM`.
- `kernel_pwm`: exige o overlay `pwm-2chan` carregado e permissao de escrita em `/sys/class/pwm`.
- `pigpio`: exige `pigpiod` funcionando e deixa erro explicito se nao conseguir conectar.
- `rpi_gpio`: usa o PWM de `RPi.GPIO`.

Observacao importante: o erro `RPi.GPIO indisponivel: ModuleNotFoundError: No module named 'RPi'` apareceu durante validacao local no macOS, onde nao ha pacote GPIO de Raspberry. Na Raspberry, esse erro so deve aparecer se `python3-rpi.gpio` nao estiver instalado ou se a venv nao enxergar os pacotes do sistema.

Resultado de teste na Raspberry em 2026-07-03:

- `RPi.GPIO` carregou via `/usr/lib/python3/dist-packages`.
- `pigpio` carregou como modulo Python.
- `pigpio_connected=False`, porque nao havia daemon `pigpiod` disponivel.
- O teste curto em `56000Hz` usou `RPi.GPIO.PWM` e detectou o feixe, mas a portadora continua saturou/perdeu leitura em aproximadamente `0.05s`, reforcando a necessidade de rajadas.

## Backend implementado

Arquivos principais:

- `app/chrono.py`
  - Controla o cronometro, leitura do sensor e emissao IR.
  - Adiciona suporte a `AGILITY_IR_PWM_BACKEND`.
  - Usa rajadas em thread dedicada (`agility-ir-burst`).
  - Usa polling logico rapido quando rajadas estao habilitadas.
  - Bloqueia calibracao durante prova `autorizado` ou `rodando`.
  - Salva e reaplica a ultima calibracao de `ir_calibration.json`.
- `app/ir_calibration.py`
  - Concentra a logica reutilizavel de calibracao.
  - Mede baseline com emissor desligado.
  - Varre frequencias.
  - Desliga o emissor por `1s` entre frequencias.
  - Testa saturacao nas frequencias sensiveis.
  - Gera recomendacao de frequencia, duty, rajada e niveis logicos.
- `rasp_scripts/testar_sensor_ir.py`
  - Passou a usar a mesma logica de `app/ir_calibration.py`.
  - Continua disponivel para teste manual no terminal da Raspberry.
- `app/main.py`
  - Adiciona endpoints:
    - `GET /config/ir/status`
    - `POST /config/ir/calibracao`
  - Adiciona calibracao opcional no startup com `AGILITY_IR_CALIBRATE_ON_STARTUP=1`.

## Frontend implementado

Arquivos principais:

- `src/views/config/ConfigView.vue`
  - Tela `/config`.
  - Mostra status do hardware IR.
  - Mostra alerta eletrico sobre GPIO de `3.3V`.
  - Mostra frequencia, duty, modo PWM, rajada, pinos e estado do feixe.
  - Inclui botao `Testar e calibrar sensor IR`.
  - Mostra recomendacao aplicada e frequencias sensiveis.
- `src/api/config.ts`
  - Cliente para os endpoints de configuracao IR.
- `src/router/index.ts`
  - Adiciona rota `/config`.
- `src/layouts/AdminLayout.vue`
  - Adiciona item `Configuracoes` no menu `Sistema`.

## Fluxo de calibracao

### Via startup

```bash
export AGILITY_IR_CALIBRATE_ON_STARTUP=1
export AGILITY_IR_CALIBRATION_APPLY=1
export AGILITY_IR_CALIBRATION_SAVE=1
export AGILITY_IR_USE_SAVED_CALIBRATION=1
```

Quando ativo, o backend bloqueia o startup ate terminar a calibracao. O resultado e salvo em `ir_calibration.json`.

### Via frontend

Na tela `/config`, clicar em `Testar e calibrar sensor IR`.

O backend:

1. Bloqueia a operacao se a prova estiver `autorizado` ou `rodando`.
2. Pausa thread de leitura do sensor.
3. Pausa emissao IR normal.
4. Executa varredura com recuperacao de `1s` entre frequencias.
5. Testa saturacao por `1s` nas frequencias sensiveis.
6. Aplica a recomendacao em runtime.
7. Salva `ir_calibration.json`.
8. Reinicia leitura do sensor e emissao por rajadas.

## Interpretacao dos estados

Com `AGILITY_SENSOR_ACTIVE_LEVEL=LOW`:

- `HIGH`: feixe alinhado, receptor esta detectando IR.
- `LOW`: feixe quebrado, sem sinal ou receptor nao detectando IR.

O backend nao deve tratar cada pulso da rajada como evento de prova. O evento deve ocorrer apenas quando o feixe logico deixa de estar alinhado por tempo suficiente, controlado por `AGILITY_SENSOR_SIGNAL_TIMEOUT`.

## Licoes aprendidas

- Sensor IR sem identificacao precisa ser tratado empiricamente; datasheets indicam principios, mas nao substituem calibracao do hardware real.
- Portadora continua pode falhar em receptores demodulados por causa de AGC ou supressao de sinais continuos.
- A barreira mais adequada e por rajadas continuas: portadora PWM rapida com pausas curtas.
- `GPIO18` e adequado para PWM por hardware na Raspberry Pi Zero 2 W.
- Alimentar o circuito do receptor em `3.3V` reduz risco de dano ao GPIO e simplifica a leitura logica.
- O teste de frequencias precisa desligar o emissor entre tentativas, pois a condicao anterior do receptor influencia a leitura seguinte.
- A faixa de `50kHz` a `60kHz` foi a mais promissora no hardware testado.
- `50000Hz` e o default pratico atual para este conjunto fisico, mas a calibracao pode escolher outro valor se o conjunto responder melhor.
- O backend precisa expor status de hardware suficiente para diagnostico remoto: frequencia, duty, backend PWM, conexao pigpio, nivel atual, estado do feixe, erros de GPIO e ultima calibracao.

## Pendencias e proximos testes

- Confirmar na Raspberry:
  - `python3-rpi.gpio` instalado.
  - `python3-pigpio` instalado.
  - se o objetivo for PWM por hardware, instalar `pigpiod` por uma fonte compativel com a imagem usada ou trocar para uma imagem que forneca o daemon.
- Confirmar que `/config/ir/status` mostra `emissor_modo=kernel.sysfs.PWM.burst` quando o overlay estiver ativo; se `pigpiod` existir, `pigpio_conectado=true` tambem pode aparecer no caminho legado.
- Executar `POST /config/ir/calibracao` com emissor e receptor alinhados.
- Verificar se `ir_calibration.json` e criado.
- Preparar prova, autorizar largada e confirmar que:
  - feixe alinhado permite autorizacao;
  - passagem de objeto inicia a prova;
  - segunda passagem finaliza a prova;
  - interrupcoes fora dos estados `autorizado` e `rodando` sao ignoradas corretamente.
