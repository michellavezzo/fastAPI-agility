# Calibracao IR com Ruido, Margem e Teste de Quebra

## Objetivo

Melhorar a calibracao automatica do sensor IR executada pelo endpoint
`POST /config/ir/calibracao` e pela tela `/config`. A calibracao deve rejeitar
respostas que tambem aparecem com o emissor desligado, identificar a frequencia
com maior margem real e priorizar frequencias que detectem rapidamente a
interrupcao do feixe.

O resultado deve continuar compativel com o startup opcional, o script
`rasp_scripts/testar_sensor_ir.py` e o arquivo `ir_calibration.json`.

## Contexto observado

- O sensor nao possui identificacao; datasheets conhecidos sao referencias de
  comportamento, nao uma especificacao garantida do componente instalado.
- O conjunto respondeu a uma faixa ampla de frequencias quando testado com
  duty de 50%, o que pode esconder a frequencia central do receptor.
- Em 50 kHz a passagem foi detectada melhor na geometria atual.
- Em frequencias menores o feixe permaneceu alinhado, mas a passagem ficou mais
  dificil de detectar e exigiu mudar a distancia do refletor.
- A operacao atual usa portadora de 50 kHz em rajadas de 6 ms ligada e 14 ms
  desligada.
- O timeout logico atual de 120 ms pode manter o feixe alinhado quando chegam
  pulsos residuais ou quando a interrupcao fisica dura menos que esse limite.

## Restricao fisica da varredura sem emissao

Com o LED IR desligado nao existe uma frequencia optica sendo emitida. Portanto,
a primeira passagem nao pode afirmar que detectou "ruido em 30 kHz" ou em outra
frequencia. Cada valor da lista identifica apenas uma janela temporal reservada
para comparacao com a mesma posicao da varredura ativa.

A API e a interface devem chamar essa informacao de janela de ruido, nunca de
frequencia de ruido.

## Fluxo de calibracao

### 1. Varredura de ruido

O emissor permanece completamente desligado durante toda a fase `noise_scan`.
Para cada frequencia candidata, a calibracao le o GPIO durante a mesma duracao
usada pela varredura ativa e armazena:

- percentual em HIGH e LOW;
- numero de transicoes;
- maior periodo continuo em cada nivel;
- indicacao de ruido confirmado.

Uma janela tem ruido confirmado quando o nivel que posteriormente representa
feixe alinhado aparece de forma continua por pelo menos 2 ms. Como o nivel de
alinhamento so e conhecido depois da leitura ativa, a confirmacao final da
contaminacao ocorre na fase de comparacao.

### 2. Varredura ativa

A fase `active_scan` mantem o comportamento de recuperacao entre frequencias:

1. desliga o emissor;
2. espera o periodo de recuperacao configurado;
3. inicia o envelope de rajadas na frequencia candidata, usando os mesmos
   tempos ON/OFF do backend;
4. espera o assentamento;
5. le o GPIO pela janela configurada;
6. desliga novamente o emissor.

Cada leitura ativa e comparada com a janela correspondente da fase de ruido.

### 3. Filtragem

Uma frequencia e descartada quando ocorrer qualquer uma destas condicoes:

- ruido confirmado na janela OFF para o nivel de alinhamento identificado;
- diferenca entre resposta ativa e resposta OFF inferior a 25 pontos
  percentuais;
- impossibilidade de produzir uma quebra logica no teste automatico;
- timeout necessario maior que 120 ms.

Cada descarte deve registrar um ou mais codigos estaveis:

- `noise_detected_off`;
- `insufficient_contrast`;
- `break_not_detected`;
- `signal_gap_too_large`.

Se todas as frequencias forem descartadas, a calibracao termina com
`ok=false`, preserva a configuracao em runtime e nao sobrescreve
`ir_calibration.json`.

### 4. Teste de margem optica

O teste `hold` de portadora continua permanece como diagnostico da supressao
interna do receptor. Supressao nesse teste nao descarta uma candidata: o
hardware de 2026-07-11 mostrou que esse comportamento e esperado e e justamente
o motivo para operar em rajadas.

Os cinco candidatos com melhor contraste na varredura em rajadas seguem para a
fase `margin_test`. Cada um e testado, tambem em rajadas, com 100%, 70%, 40% e
20% do duty solicitado para a calibracao.

O menor duty que ainda mantiver resposta valida vira a metrica
`minimum_stable_duty`. Ela serve apenas para ordenar frequencias. A recomendacao
operacional continua usando o duty originalmente solicitado, evitando reduzir
o alcance depois da calibracao.

Essa etapa diferencia frequencias que atingem 100% apenas porque o retorno
optico esta forte. A frequencia que funciona com menor energia possui maior
margem no conjunto emissor, refletor e receptor instalado.

### 5. Teste no envelope operacional

Os finalistas sao testados na fase `burst_test` com os mesmos tempos usados
pelo backend: 6 ms de portadora e 14 ms sem portadora, salvo configuracao
explicita diferente.

A emissao do envelope roda em uma thread dedicada durante o teste. A thread de
calibracao le o GPIO e mede:

- percentual de sinal durante a emissao em rajadas;
- maior intervalo entre pulsos validos;
- estabilidade durante pelo menos 1 segundo.

A thread deve ser sempre finalizada no bloco de limpeza, inclusive em caso de
erro ou cancelamento.

### 6. Teste automatico de quebra

Depois de estabilizar o candidato em rajadas:

1. o emissor e desligado por 250 ms;
2. a leitura continua durante todo o periodo;
3. a calibracao mede o tempo ate a ausencia de sinal ser confirmada;
4. pulsos residuais durante o periodo OFF sao contabilizados;
5. o emissor volta a emitir em rajadas;
6. a calibracao mede o tempo de reacquisicao.

O teste simula eletricamente uma interrupcao total. Ele nao comprova que um
objeto real bloqueia todos os caminhos opticos entre emissor, ambiente e
refletor. O resultado deve conter `physical_break_validated=false` para deixar
essa limitacao explicita.

### 7. Timeout recomendado

Para cada finalista, o timeout candidato e calculado por:

```text
max(3 * periodo_da_rajada, 2 * maior_intervalo_observado + 0.005)
```

O periodo da rajada e `burst_on + burst_off`. O valor nunca pode ser menor que
o minimo de seguranca ja aplicado pelo `Chronometer`. Candidatos que precisem
de timeout superior a 120 ms sao rejeitados.

Com envelope estavel de 6 ms / 14 ms e intervalo observado proximo de 20 ms, o
timeout esperado e 60 ms.

## Escolha da recomendacao

A ordenacao deve usar, nesta ordem:

1. ausencia de ruido confirmado;
2. teste de quebra aprovado;
3. menor intervalo maximo entre pulsos em rajadas;
4. menor tempo de liberacao e reacquisicao;
5. proximidade da frequencia preferida quando essas metricas forem equivalentes.

Tempos dentro de `5ms` sao tratados como equivalentes na selecao final para nao
transformar jitter de polling em vantagem falsa. Depois que uma candidata
supera o piso de contraste, seu percentual bruto permanece diagnostico: ele
tambem reflete a largura do pulso demodulado e nao deve ser interpretado como
qualidade automaticamente maior. `minimum_stable_duty` tambem permanece
diagnostico: valor menor representa maior reserva optica, mas reserva excessiva
pode facilitar caminhos refletidos ao redor do objeto e nao deve ser premiada
automaticamente sem um teste fisico.

A preferencia atual por 50 kHz deixa de mascarar diferencas mensuraveis, mas
continua produzindo resultado deterministico quando os candidatos forem
equivalentes.

## Contrato do resultado

O resultado existente permanece retrocompativel e ganha:

- `noise_scan`: janelas OFF e estatisticas;
- `rejected`: frequencias descartadas e motivos;
- `margin`: resultados do teste de duty dos finalistas;
- `burst`: estabilidade no envelope operacional;
- `break_tests`: liberacao, pulsos residuais e reacquisicao;
- `diagnostics`: resumo legivel e limitacao do teste fisico.

A recomendacao ganha:

- `minimum_stable_duty`;
- `burst_max_signal_gap`;
- `break_release_s`;
- `reacquire_s`;
- `physical_break_validated`, sempre `false` nessa calibracao automatica.

Campos existentes, incluindo `frequency_hz`, `duty_cycle`, `burst_on`,
`burst_off`, `sensor_active_level` e `sensor_signal_timeout`, devem continuar
presentes.

## Progresso e frontend

O status da calibracao deve usar estas fases:

- `noise_scan`;
- `active_scan`;
- `hold`;
- `margin_test`;
- `burst_test`;
- `break_test`;
- `select`.

A tela `/config` deve manter o botao existente e mostrar no resultado:

- quantidade de janelas OFF contaminadas;
- frequencias rejeitadas e motivo;
- menor duty estavel dos finalistas;
- tempo de liberacao e reacquisicao;
- timeout recomendado;
- aviso de que a passagem fisica ainda precisa ser validada.

## Tratamento de erros e concorrencia

- A calibracao continua bloqueada durante prova autorizada ou em andamento.
- Apenas uma calibracao pode rodar por vez.
- O processamento normal do GPIO e a emissao normal sao pausados antes do
  teste e reiniciados ao final.
- Toda saida PWM deve ser desligada em `finally`.
- A thread de envelope deve ser sinalizada, aguardada e descartada em
  `finally`.
- Falhas nao podem apagar a ultima calibracao valida.

## Testes automatizados

Os testes usam GPIO, emissor e relogio falsos, sem exigir Raspberry Pi:

1. candidato com sinal confirmado na janela OFF e descartado;
2. candidato limpo com contraste suficiente permanece elegivel;
3. candidato que alinha mas nao libera durante o OFF e descartado;
4. candidato com pulsos residuais que impedem a quebra e descartado;
5. quando todas as respostas ativas chegam a 100%, vence o candidato que
   permanece estavel no menor duty;
6. timeout de rajada estavel de 20 ms e calculado em 60 ms;
7. timeout acima de 120 ms invalida o candidato;
8. nenhuma recomendacao valida preserva o arquivo anterior;
9. o formato antigo do resultado continua disponivel para o frontend.

O frontend deve passar por verificacao de tipos e build de producao.

## Validacao na Raspberry Pi

Depois dos testes locais e do fluxo de commit, push e pull no backend da
Raspberry:

1. executar a calibracao pela tela `/config`;
2. confirmar que a fase OFF ocorre com o LED IR apagado;
3. verificar os descartes e a frequencia recomendada;
4. confirmar que `emissor_modo` continua usando PWM de hardware;
5. verificar o timeout aplicado;
6. iniciar uma prova e executar passagens fisicas com objeto opaco;
7. comparar eventos aceitos, eventos ignorados e quebras logicas.

O teste fisico final exige coordenacao com o usuario porque desligar o emissor
nao reproduz reflexos que possam contornar o objeto real.

## Referencias

- Vishay TSOP382/TSOP384: https://www.vishay.com/docs/82491/tsop382.pdf
- Vishay TSSP Sensor Kit: https://www.vishay.com/en/doc?80345=
- Omron Photoelectric Sensors Technical Guide:
  https://www.ia.omron.com/data_pdf/guide/43/photoelectric_tg_e_8_1%28engineering%29.pdf
