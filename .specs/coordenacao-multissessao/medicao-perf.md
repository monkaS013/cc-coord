# Medição do custo do caminho quente dos hooks (T-11, RNF-04)

**Task:** `T-11` (`tasks.md`) · **Requisito:** RNF-04 — custo por hook < 150 ms (p95)
**Data:** 2026-09-11 · **Teste:** `tests/test_perf.py` (12 testes, todos como subprocesso real)
**Máquina:** a mesma usada no dia a dia (notebook Windows, com o próprio Claude Code e o resto do
ambiente de trabalho rodando ao lado — não um servidor isolado; ver §5).

---

## Veredito (resumo em uma frase)

**RNF-04 NÃO está cumprido de forma confiável hoje.** Rodei a suíte inteira **4 vezes seguidas**
(mesma máquina, minutos de intervalo) porque a primeira rodada já mostrava números incomuns —
e o teste de gate (`@spec:AC-005`, pior caso medido) **ficou vermelho em 2 das 4 rodadas** (p95:
108 / **227** / 108 / **213** ms). Além do gate, **11 dos 12 cenários da suíte estouraram 150 ms
de p95 em pelo menos uma das 4 rodadas** — inclusive cenários triviais, sem conflito nenhum. Isto
está sendo medido, não estimado: o teto de 150 ms está sendo decidido mais pela variação de carga
do sistema e pelo custo fixo de importar o pacote `ccoord` do que pelo volume de claims/sessões
em si (§5).

---

## 1. Método

- Cada hook roda como **subprocesso de verdade** (`subprocess.run`, payload JSON no stdin),
  exatamente como o harness chama — inclui o custo de start do interpretador Python 3.14
  (`C:/Python314/python.exe`), que uma medição in-process esconderia.
- **N = 20 execuções por cenário** (mínimo pedido), reportando **p50 e p95** — nunca a média.
  Percentil por interpolação linear entre os postos mais próximos (método "linear" padrão do
  numpy), implementado em `tests/test_perf.py::_percentil` (stdlib apenas, RNF-01).
- **Fixtures em `tempfile`**, `CCOORD_HOME`/`CCOORD_SESSIONS_DIR` isolados por cenário — nada
  tocado em `~/.claude/coord` real nem em `~/.claude/sessions` real.
- Sessões "vivas" no registro são **processos reais** (`subprocess.Popen` dormindo, PID +
  `procStart` reais via `sessions._query_process_creation_ticks`) — não PIDs inventados —, para
  que `sessions.peers()` percorra o mesmo caminho de código (`OpenProcess`/`GetProcessTimes`
  reais) que rodaria em produção.
- **Achado que mudou o desenho da medição:** a primeira rodada já mostrava números discrepantes
  de cenário para cenário sem padrão óbvio, e o teste de gate passou na 1ª e falhou na 2ª rodada
  imediatamente seguinte (mesmíssimo código, mesmíssima máquina). Decidi rodar a suíte completa
  **4 vezes** em vez de relatar só uma rodada — o objetivo aqui é medir, não escolher a rodada
  mais bonita.

---

## 2. Resultados por cenário (4 rodadas independentes, N=20 cada)

Valores em ms. "Pior p95" = maior das 4 rodadas — o número que decide se o requisito está
garantido, não o melhor caso.

| Cenário | R1 p95 | R2 p95 | R3 p95 | R4 p95 | pior p95 | < 150 ms nas 4? |
|---|---|---|---|---|---|---|
| **GATE** `pre_write` Edit **com conflito**, 8000 linhas, 30 claims, 10 sessões | 108,1 | **226,9** | 108,1 | **212,9** | 226,9 | NÃO (2/4) |
| `pre_bash` bind livre, 3 claims, 2 sessões | 144,1 | **214,8** | 141,4 | 146,3 | 214,8 | NÃO (1/4) |
| `pre_bash` bind livre, 10 claims, 2 sessões | 144,2 | **175,5** | 128,4 | 126,5 | 175,5 | NÃO (1/4) |
| `pre_bash` bind livre, 30 claims, 2 sessões | **200,1** | **263,0** | 127,4 | 142,4 | 263,0 | NÃO (2/4) |
| `pre_bash` bind livre, 10 claims, 10 sessões | **155,8** | **257,0** | **157,8** | **231,9** | 257,0 | NÃO (4/4) |
| `pre_bash` bind com conflito (peer + porta vizinha ocupada), 10c/10s | 125,9 | 153,3 | 141,0 | **189,2** | 189,2 | NÃO (2/4) |
| `pre_write` Edit sem conflito (50 linhas), 3 claims, 2 sessões | 113,7 | 137,9 | **151,4** | 136,9 | 151,4 | NÃO (1/4, raspando) |
| `pre_write` Edit sem conflito, 10 claims, 2 sessões | 120,8 | 133,9 | 121,3 | 143,1 | 143,1 | **SIM (4/4)** |
| `pre_write` Edit sem conflito, 30 claims, 2 sessões | 128,7 | 133,3 | 135,3 | **159,7** | 159,7 | NÃO (1/4) |
| `pre_write` Edit sem conflito, 10 claims, 10 sessões | 130,1 | 137,7 | 117,7 | **167,6** | 167,6 | NÃO (1/4) |
| Q-005: Edit em arquivo ~50 linhas | 113,0 | **154,2** | **164,7** | 124,2 | 164,7 | NÃO (2/4) |
| Q-005: Edit em arquivo ~8000 linhas (tam. real do `web/app.js`) | 119,0 | **184,8** | **152,6** | **162,3** | 184,8 | NÃO (3/4) |

**Só 1 dos 12 cenários** (`pre_write` sem conflito, 10 claims, 2 sessões — o caso "mais limpo"
do grid) ficou **sempre** abaixo de 150 ms nas 4 rodadas, e mesmo assim com pouca folga (máximo
143 ms de p95). **Todos os outros 11 estouraram em pelo menos uma rodada.**

---

## 3. Q-005 — custo de derivar a faixa de linha do `old_string`

**Pergunta (design.md §7):** derivar a faixa de linha custa uma leitura de arquivo no caminho
quente; se passar de 150 ms, cair para `range: null` (arquivo inteiro).

**Medido:** comparando arquivo de **~50 linhas** com arquivo de **~8000 linhas** (tamanho real do
`web/app.js` que motivou esta feature), com `old_string` posicionado perto do FIM do arquivo (pior
posição para uma busca linear via `str.find`):

| Rodada | 50 linhas (p95) | 8000 linhas (p95) | diferença |
|---|---|---|---|
| 1 | 113,0 ms | 119,0 ms | +6,0 ms |
| 2 | 154,2 ms | 184,8 ms | +30,6 ms |
| 3 | 164,7 ms | 152,6 ms | **-12,1 ms** (dentro do ruído) |
| 4 | 124,2 ms | 162,3 ms | +38,1 ms |

**Resposta:** a diferença atribuível ao TAMANHO do arquivo fica entre **-12 ms e +38 ms**
(média ≈ +16 ms) — bem menor que a variação de rodada para rodada do MESMO cenário exato, que
chegou a **+70 ms** (ex.: arquivo de 8000 linhas: 119,0 → 184,8 → 152,6 → 162,3 ms). **Não é este
o mecanismo que ameaça o orçamento de 150 ms.** Cair para `range: null` (degradar a granularidade
para D-02) não resolveria o estouro observado em §2 — o gargalo dominante está em outro lugar
(§5), não na leitura+busca do arquivo. Achado para quem decidir otimizar (não é decisão desta
task): não vale a pena abandonar a faixa de linha por causa deste número especificamente.

---

## 4. Q-006 — custo do watcher com 10 e 100 caminhos vigiados

**Não foi possível medir isto de forma honesta a partir deste repositório, e por isso não foi
inventado nenhum número.** Motivo:

- O "watcher" do design é o `chokidar` **dentro do harness fechado** (Node.js) — nenhum código
  deste repositório controla quantos caminhos ele vigia. O único ponto de contato é
  `hooks/coord_file_changed.py`, que reage a UM evento por vez; ele não recebe "quantos paths
  estão sendo vigiados" como entrada, e não há como forçar o harness a vigiar 10 ou 100 caminhos
  a partir de um teste local (isso é decidido pelo `SessionStart`/`CwdChanged` do harness via
  `watchPaths` no retorno — mecanismo já medido em `medicao-hooks.md`, mas não reproduzível fora
  de uma sessão real do Claude Code).
- O único dado empírico que existe é o de `medicao-hooks.md` (Task 1, 11/09): o `FileChanged`
  disparou em **~0,62 s** para UMA alteração externa, em UMA sessão real, com o número de paths
  vigiados daquela sessão específica (não controlado nem variado). Isso não é um teste de escala
  por número de paths — é um único ponto, medido em condição não controlada.
- Fabricar um número para "10 vs 100 paths" sem instrumentação real do harness seria uma
  estimativa disfarçada de medição, o que a task pede explicitamente para não fazer.

**Conclusão:** Q-006 continua em aberto. Responder de verdade exige instrumentação do lado do
harness (fora do escopo de `ccoord`) ou um probe dedicado tipo `tools/probe/` (T-01), não um
teste de subprocesso deste pacote.

---

## 5. Por que o orçamento de 150 ms está tão apertado (achado extra, relevante para quem for otimizar)

Para entender por que cenários TRIVIAIS já flertam com o limite, medi dois pisos de comparação
fora da suíte de testes (mesma máquina, mesma sessão de medição):

| O quê | p50 | p95 |
|---|---|---|
| `python -c "pass"` (só o interpretador, nada de `ccoord`) | 32,6 ms | 35,1 ms |
| `import ccoord.hookio, sessions, claims, classify, policy` (import puro, sem lógica nenhuma) | 88,3 ms | 100,6 ms |

**O import do pacote `ccoord` sozinho já consome 88-101 ms** — antes de ler qualquer claim,
sessão ou arquivo. Isso deixa uma folga real de só **50-60 ms** dentro do orçamento de 150 ms
para TODA a lógica de `classify`/`claims`/`sessions`/`policy`/`hookio` MAIS a variação normal do
sistema operacional. Nas 4 rodadas da suíte, a variação de rodada para rodada do MESMO cenário
exato já consumiu sozinha até **100 ms** de diferença de p95 (`pre_bash` bind livre, 10 claims,
10 sessões: 155,8 → 257,0 → 157,8 → 231,9 ms). Ou seja: o teto de 150 ms está sendo decidido mais
pela variação de carga do sistema (e pelo custo fixo de import do Python) do que pelo tamanho de
`N claims`/`M sessões` em si.

**Efeito de `N` claims em disco (3/10/30), isolando de sessões:** **sem efeito consistente e
reprodutível** nas 4 rodadas — em 2 rodadas o p95 sobe de 3→30 claims, em 2 rodadas fica achatado
ou desce. Bate com a leitura do código: `claims.claim()` para uma chave nova é um
`os.open(O_CREAT|O_EXCL)` único sobre UM arquivo (nunca varre o diretório inteiro); o volume de
OUTROS claims em disco não entra na conta do caminho quente hoje, só entraria se algum dia
`overlapping()`/`sweep()`/`release()` passassem a rodar nesse caminho.

**Efeito de `M` sessões no registro (2/10):** **efeito real, na mesma direção nas 4 rodadas** —
`sessions.peers()` percorre `~/.claude/sessions/*.json` e chama `OpenProcess`/`GetProcessTimes`
(syscall real do Windows) por ARQUIVO. Foi o eixo com o salto mais consistente: `pre_bash` bind
livre, 10 claims, subiu de 2→10 sessões em TODAS as 4 rodadas (+11,6 / +81,5 / +29,4 / +105,4 ms)
— sempre para cima, nunca para baixo, e às vezes o maior fator isolado da suíte inteira.

---

## 6. O que este relatório NÃO fez (por restrição da task)

- Não otimizou nada — nenhuma mudança em `src/ccoord/*.py` nem `hooks/*.py`.
- Não relaxou o limite de 150 ms para fazer o teste de gate passar; ele fica vermelho quando o
  número real estoura (2 das 4 rodadas provaram isso).
- Não criou nem alterou nenhum arquivo fora de `tests/test_perf.py` e deste relatório.

## 7. Rastreabilidade

- Teste de gate: `tests/test_perf.py::TestGateRnf04PiorCasoP95::test_gate_p95_hot_path_pior_caso_menor_que_150ms`
  (`@spec:AC-005`) — falha quando o p95 do pior caso medido passa de 150 ms (RNF-04). **É
  instável por natureza** (mede carga real da máquina): passou em 2 das 4 rodadas locais, falhou
  nas outras 2. Isso não é bug do teste — é o próprio requisito não estando garantido.
- 12 testes ao todo em `tests/test_perf.py` (`grep -c 'def test_'` confirma).
- Ver `.specs/coordenacao-multissessao/tasks.md` T-11 e `design.md` §7 (Q-005/Q-006) para o
  enunciado original.

---

## Otimização (T-017)

**Causa raiz confirmada** (não hipótese): subir o Python e importar
`ccoord.policy + claims + sessions + classify` custava **p50 94ms / p95 112ms** num
subprocesso isolado, contra **31ms** de um `python -c pass` vazio — ~63ms eram custo FIXO
de import, não de lógica. Medido com `-X importtime`: o grosso vinha de `dataclasses`
(que arrasta `inspect`/`dis`/`tokenize`/`ast`, ~10ms), `ctypes`/`ctypes.wintypes` (~10ms,
usados por `sessions.py`/`claims.py` mesmo quando ninguém precisava checar liveness),
`pathlib` (~4-5ms) e `typing` (~2ms) — todos importados **no topo do módulo**,
incondicionalmente, mesmo no caso "sem conflito nenhum" (o caso esmagadoramente comum).

### O que mudou

1. **`dataclasses` → classes simples** em `ccoord.sessions.Session`, `ccoord.claims.Owner`/
   `Claim`/`ClaimResult`, `ccoord.classify.Resource`, `ccoord.policy.Decision`. Mesmo
   contrato (`to_dict`/`from_dict`, mesma ordem de campo/defaults) — só sem o decorator,
   que paga `inspect` de graça a troco de nada no nosso caso (nenhum código usa
   equality/hash estrutural dessas classes).
2. **Import tardio (dentro da função) para `ctypes`/`ctypes.wintypes`** em
   `sessions._query_process_creation_ticks`/`_pid_exists` e `claims._win_creation_ticks` —
   só paga quem de fato consulta liveness de um PID (i.e., quem chama `sessions.peers()`,
   ou quem disputa um claim já existente em `claims.claim()`).
3. **`pathlib` só na API pública `sessions.sessions_dir()`** (contrato de
   `tests/test_sessions.py`/`ccoord/cli.py`, que usa `.exists()`) — uso interno
   (`me()`/`peers()`/`_iter_session_files()`) passou a usar uma string simples
   (`_sessions_dir_str()`), sem tocar `pathlib`.
4. **`typing` removido** de `hookio.py`/`sessions.py`/`claims.py`/`classify.py`/`policy.py`
   onde só servia para anotação — `from __future__ import annotations` já deixa essas
   anotações como string, nunca avaliadas em runtime; importar `typing` só para isso era
   custo puro.
5. **`policy.py` parou de importar `ccoord.claims`/`ccoord.classify`/`ccoord.sessions`**
   (usados só em anotação, nunca instanciados nem checados via `isinstance`).
6. **Fast path nos hooks** (`hooks/coord_pre_write.py`, `hooks/coord_pre_bash.py`):
   `sessions.peers()` — o eixo mais caro (liveness real via ctypes, por ARQUIVO de sessão
   em disco) — passou a ser **adiado e só calculado se algum recurso do lote realmente
   precisar dele**: para `file`/`port`/`server`, `policy.decide()` só consulta `peers`
   quando já existe um `owner_claim` (peer detém o recurso); para `git`/`db`, `peers` é
   sempre necessário (a decisão não usa claim, usa peer no mesmo repo) — condições
   espelhadas 1:1 do dispatch de `policy.decide()`, não uma regra nova. `claims.claim()`/
   `_marcar_escrita_propria()`/`_consumir_carimbo_pendente()` continuam rodando SEMPRE, sem
   exceção — nenhum efeito colateral foi pulado, só o cálculo de `peers_vivas` foi adiado.
   Prova de equivalência: `tests/test_entrypoints.py::TestCaminhoRapidoNaoEngoleConflito`
   (2 testes novos — um com conflito real de peer viva, outro sem conflito nenhum).

### Import isolado, antes/depois (subprocesso `python -c "from ccoord import ..."`, N=20)

| | p50 | p95 |
|---|---|---|
| ANTES (medição original) | 94ms | 112ms |
| DEPOIS | 49ms | 63ms |

### p95 por cenário, antes (pior de 4 rodadas, T-11) x depois (T-017, single-round pós-otimização)

| Cenário | ANTES (pior p95, 4 rodadas) | DEPOIS (p95) |
|---|---|---|
| GATE (`pre_write` Edit com conflito, 8000L/30c/10s) | 226,9 ms | 106-149 ms (3 rodadas, ver abaixo) |
| `pre_bash` bind livre, 3c/2s | 214,8 ms | 99,5 ms |
| `pre_bash` bind livre, 10c/2s | 175,5 ms | 97,3 ms |
| `pre_bash` bind livre, 30c/2s | 263,0 ms | 94,4 ms |
| `pre_bash` bind livre, 10c/10s | 257,0 ms | 113,3 ms |
| `pre_bash` bind COM conflito (10c/10s) | 189,2 ms | 105-178 ms (instável, ver nota) |
| `pre_write` sem conflito, 3c/2s | 151,4 ms | 73,1 ms |
| `pre_write` sem conflito, 10c/2s | 143,1 ms | 78,9 ms |
| `pre_write` sem conflito, 30c/2s | 159,7 ms | 71,4 ms |
| `pre_write` sem conflito, 10c/10s | 167,6 ms | 89,8 ms |
| Q-005 arquivo ~50 linhas | 164,7 ms | 72,5 ms |
| Q-005 arquivo ~8000 linhas | 184,8 ms | 85,9 ms |

Nota sobre `pre_bash` bind COM conflito: é o único cenário onde `sessions.peers()`
continua sendo pago de propósito (há conflito real — teria que pagar mesmo no lento).
Rodadas isoladas deram 136,6ms e 105,5ms de p95; uma rodada logo após rodar os outros 10
cenários no mesmo processo de teste (`tools/bench_hooks.py` inteiro) deu 177,6ms — indício
de que o SO/antivírus ainda estava "aquecido" pelas execuções anteriores, não uma regressão
determinística. Fica documentado, não escondido.

### Suíte de testes: reorganização (`tests/run_tap.py` x `tools/bench_hooks.py`)

A suíte inteira (`tests/run_tap.py`) rodava os 12 cenários de perf (N=20 cada) TODA vez —
sozinho isso já beirava/passava de 2 minutos, no gesto mais frequente do projeto. Os 11
cenários que não são o gate (grade de N claims/M sessões sem conflito, conflito de porta,
Q-005) viraram `tools/bench_hooks.py` — reaproveita toda a infra de `tests/test_perf.py`
(fixtures, pool de processos, percentis) por import direto, mas não fica em `tests/` nem
segue `test_*.py`, então `unittest.discover()` nunca o pega. `tests/test_perf.py` manteve
SÓ o teste de gate (`TestGateRnf04PiorCasoP95`).

**Tempo da suíte:** ~36,6s (baseline, antes de qualquer mudança) → **~6,5-7,1s** depois
(3 de 4 rodadas medidas; uma rodada teve concorrência externa de CPU no laptop — processos
alheios do próprio Vinicius, `serve.py`/`widget.pyw`, não deste projeto — e subiu para
34min com timeout num teste; refeita imediatamente após, voltou a 6,7s/exit 0).

### As 3 rodadas de verificação pedidas (`tests/run_tap.py`)

| Rodada | Exit code | p95 do GATE | Observação |
|---|---|---|---|
| 1 | 0 | 137,68 ms | 7,15s de suíte |
| 2 | 0 | 148,89 ms | 6,48s de suíte — passou raspando |
| 3 (original) | **1** | 167,99 ms | 34min de suíte; 1 timeout de subprocess + gate estourado — causa confirmada: outros processos do Vinicius (`serve.py` do app interno, `widget.pyw` do Monitor de Uso Claude) consumindo CPU pesadamente no mesmo laptop (`Get-CimInstance Win32_Process` confirmou os PIDs e as linhas de comando) |
| 3 (repetida, mesma máquina, minutos depois) | 0 | 106,37 ms | 6,69s de suíte |

**Veredito:** RNF-04 passou a ser cumprido no caminho comum (import ~2x mais rápido,
scan de liveness adiado no caso sem conflito), mas continua **instável sob carga externa
pesada da máquina** — exatamente como a medição original já dizia ("o teto de 150ms está
sendo decidido mais pela variação de carga do sistema... do que pelo tamanho de N
claims/M sessões"). Isso não é um problema resolvido por esta task (é característica do
ambiente, não do código) — só ficou MENOS provável de aparecer, porque a margem até 150ms
aumentou bastante (de ~0-20ms de folga para ~50-80ms de folga no caso comum).

---

## Verificação independente (11/09, sessão orquestradora)

Não aceitei os números do relatório da T-017 sem remedir. Três rodadas completas da suíte, carga da
máquina em ~1%, extraindo o resumo do próprio cenário de gate (`CCOORD_PERF_RESULT_PATH`):

| Rodada | p50 | p95 |
|---|---|---|
| 1 | 76,85 ms | **127,95 ms** |
| 2 | 79,17 ms | **100,42 ms** |
| 3 | 78,78 ms | **99,61 ms** |

Cenário medido: `coord_pre_write.py`, `Edit` **com conflito** de peer viva — o pior caso do caminho
quente. Antes da otimização: p95 de 213-227 ms. **Limite: 150 ms.**

Custo de import remedido por mim (N=15, subprocesso): **p50 59 ms / p95 97 ms**, contra 94/112 antes.

Suíte completa: **7,1-8,0 s** em 4 rodadas, todas exit 0 (antes: 36,6 s).

**RNF-04: cumprido**, com margem de 22-50 ms no p95 do pior caso sob carga normal. A ressalva que fica
registrada, não escondida: sob carga externa pesada da própria máquina (medido durante a T-017 com
`serve.py` do Data Center e `widget.pyw` do Monitor de Uso saturando CPU) o p95 chegou a 168 ms e o
gate reprovou. Isso é característica do ambiente — laptop compartilhado com os próprios serviços do
Vinicius —, e o gate acusar nesse momento é o comportamento correto, não um falso positivo a
silenciar. Nenhum limite de teste foi afrouxado em nenhum momento.

---

## O piso do interpretador NÃO é constante — e o gate passou a medi-lo (12/09)

**O problema:** o RNF-04 é um limite ABSOLUTO (150 ms), mas o custo de subir o Python nesta máquina
varia ao longo do dia:

| Momento | `python -c pass` (p50) |
|---|---|
| 11/09 ~21h | **31 ms** |
| 11/09 ~23h | **43–69 ms** (4 amostras, CPU a 17%) |
| 12/09 (esta medição) | **31,58 ms** |

Ou seja: **até 46% do orçamento de 150 ms pode ser consumido antes da primeira linha de código nossa.**
Isso quase me fez registrar uma regressão inexistente — o p50 do gate havia subido de 78 para 131-145 ms
e o delta dos nossos imports estava *idêntico* (~31 ms). O que mudou foi o piso.

**No mesmo dia, o inverso também aconteceu:** o gate pegou uma regressão que ERA nossa —
`ctypes.WinDLL("kernel32", use_last_error=True)` recarrega a DLL a cada chamada, ao contrário de
`ctypes.windll.kernel32`, que é cacheado. Corrigida com cache no módulo (só `WinDLL` expõe
`use_last_error`, que é o que distingue `ACCESS_DENIED` de "processo inexistente").

**O que mudou no gate:** ele agora mede `python -c pass` na MESMA rodada e põe o número na mensagem de
falha, junto com quanto o hook custou *acima* do piso. **O critério não mudou** — continua p95 ≤ 150 ms
absolutos, que é requisito do dono. O que mudou é que uma reprovação agora se lê:

```
RNF-04 violado: p95=84.18ms > 1.0ms (p50=71.67ms, max=216.22ms, n=20).
PISO do interpretador nesta rodada: 31.58ms -- o codigo do hook respondeu por ~40.09ms acima dele.
Piso normal: a reprovacao aponta para o CODIGO.
```

(saída real, com o limite temporariamente mutado para 1 ms para forçar a falha e provar a mensagem;
arquivo restaurado byte a byte, sha256 conferido)

**Número que importa para a decisão:** o hook custa **~40 ms de código próprio**. O resto é o
interpretador. Com o `block_env_edit.py` do dono no mesmo evento (~43 ms, também um processo Python),
o custo por `Edit` é dominado por *dois* starts de interpretador, não pela nossa lógica.
