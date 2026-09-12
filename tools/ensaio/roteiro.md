# Ensaio ponta a ponta — duas sessões reais

**Por que este ensaio não é opcional.** Os 17 critérios têm prova PASS e o `onp-spec audit --ci` está
verde, e isso **não** significa que a feature funciona. A rastreabilidade liga critério → teste, nunca
critério → caminho real: o AC-007 é o exemplo vivo (o teste exercita a função de decisão, que recebe o
contexto pronto; quem *produz* o contexto não existia — T-016). Só duas sessões de verdade, na mesma
máquina, exercitam o caminho inteiro: harness → hook → registro → política → razão que chega ao modelo.

**Pré-requisitos**
1. T-012 executada (hooks instalados no `settings.json` global) — e isso exige aprovação do Vinicius.
2. Duas janelas de terminal, cada uma com uma sessão nomeada: `claude --name ensaio-a` e `claude --name ensaio-b`.
3. `ccoord status` respondendo nas duas.

**Regra de ouro da verificação:** *o modelo é testemunha não confiável do que recebeu.* Medido em
11/09 — a sessão deixou de listar contexto que o transcript prova ter chegado. Então **toda evidência
sai de `grep` no transcript JSONL**, em `~/.claude/projects/<slug>/<session-id>.jsonl`, nunca de
perguntar à sessão "você recebeu o aviso?".

---

## Cenário 1 — O mapa no início da sessão (AC-012)

| | |
|---|---|
| **Ação** | Com a `ensaio-a` aberta, abrir a `ensaio-b`. |
| **Esperado** | A `ensaio-b` recebe, no `SessionStart`, a lista de peers vivas com `cwd` e os recursos ocupados. |
| **Evidência** | `grep -o "ccoord" <transcript da B>.jsonl \| head` e confirmar que o bloco de contexto cita **`ensaio-a` e o cwd dela**. |
| **PASS** | o nome e o cwd da A aparecem no transcript da B. |
| **FAIL comum** | hook não registrado (conferir `claude --debug`), ou `additionalContext` vazio porque a A não tinha claim nenhuma — nesse caso o mapa deve ainda assim listar a sessão. |

## Cenário 2 — Arquivo compartilhado, faixas disjuntas (AC-005)

| | |
|---|---|
| **Ação** | A edita as linhas ~10-20 de um arquivo de teste; B edita as linhas ~200-210 do **mesmo** arquivo. |
| **Esperado** | B recebe **aviso curto** e a edição **acontece**. Nada de `deny`. |
| **Evidência** | a edição de B está no disco; o transcript de B contém o aviso nomeando a A. |
| **PASS** | os dois `Edit` no disco + aviso presente. |
| **Por que importa** | é o caso que funcionou na prática em 11/09 (duas sessões no mesmo `web/app.js`, zero conflito). Se aqui sair `deny`, a feature virou atrito sem evitar perda — o defeito que o Vinicius nomeou. |

## Cenário 3 — Reescrita sobre arquivo com dono (AC-017)

| | |
|---|---|
| **Ação** | A mantém claim no arquivo; B usa **`Write`** (arquivo inteiro) nele. |
| **Esperado** | aviso **mais forte** que o do cenário 2, sugerindo `Edit` cirúrgico. Ainda assim permitido. |
| **Evidência** | comparar os dois textos de aviso no transcript de B — o do `Write` tem de ser distinguível do de `Edit`. |

## Cenário 4 — Kill do browser (AC-003) — **o cenário que justifica a feature**

| | |
|---|---|
| **Ação** | A adquire `browser:profile-b` (`ccoord`, ou navegando pelo MCP). B tenta `Stop-Process` casando o perfil. |
| **Esperado** | **`deny`**, com razão que (a) nomeia a A, (b) indica o servidor alternativo, (c) proíbe o kill, (d) manda perguntar ao Vinicius. |
| **Evidência** | `grep -c "permissionDecisionReason" <transcript B>` e ler a razão inteira; **e** conferir que o processo do browser **continua vivo** (`Get-CimInstance Win32_Process`, contar antes/depois). |
| **PASS** | ação bloqueada **e** processo intacto **e** razão com os 4 elementos. |
| **⚠️ Armadilha** | `exit 1` no hook faria a razão aparecer e a ação **passar**. Por isso o teste conta processos, não só lê texto. |

## Cenário 5 — Git write com peer viva (AC-007 + T-016)

| | |
|---|---|
| **Ação** | as duas sessões num mesmo repo de teste, com um commit feito pela A; B tenta `git commit`. |
| **Esperado** | `deny` citando **o hash do commit da A** e prescrevendo `git log origin/<br>..HEAD`. |
| **Evidência** | o hash real da A aparece na razão no transcript de B. |
| **Hoje falha** | enquanto a T-016 não entrar, a recusa sai sem citar o commit. **Este cenário é o teste de aceitação da T-016.** |

## Cenário 6 — Alteração externa detectada (AC-015 + AC-016)

| | |
|---|---|
| **Ação** | B tem claim num arquivo; A altera esse arquivo. Depois B faz qualquer `Edit`. |
| **Esperado** | no `Edit` seguinte, B é avisada de que o arquivo mudou, **com arquivo e horário**. |
| **Evidência** | carimbo em `<CCOORD_HOME>/changed/` + aviso no transcript de B. |
| **Contraprova obrigatória (AC-015)** | B altera um arquivo **dela mesma** e **não** recebe aviso. Sem essa metade, o sensor pode estar avisando sobre tudo — inclusive sobre a própria sessão — e o cenário passaria por acidente. |

## Cenário 7 — A fila do recurso (o fluxo completo do §8 da spec)

1. A detém `browser:profile-b`; B é recusada (cenário 4).
2. B manda `SendMessage` para A pedindo o recurso + `notify_when_idle: true`.
3. A encerra.
4. B recebe o aviso e adquire a lease.

**PASS:** nada foi morto, nada foi refeito, e B obtém o recurso sem intervenção do Vinicius.

**⚠️ Duas armadilhas medidas antes:**
- `notify_when_idle` avisa que a sessão **ficou ociosa**, não que **soltou o browser** — ela pode
  encerrar o turno sem `browser_close`. Quem espera deve **tentar abrir** e, se o lock voltar com
  processo recente, **pedir o close**, nunca matar.
- O `browser_close` **não mata o processo** (o MCP o mantém vivo para reaproveitar). Então "processo
  existe" não prova uso; o que prova é processo **recente** + alguém falhando ao abrir.

## Cenário 8 — Degradação (AC-010)

| | |
|---|---|
| **Ação** | apagar/corromper `<CCOORD_HOME>` e, em seguida, (a) fazer um `Edit` e (b) tentar um kill. |
| **Esperado** | (a) `allow` com erro no `events.log`; (b) `deny`. |
| **PASS** | o turno não quebra em nenhum dos dois, e o kill é recusado mesmo sem registro legível. |

---

## Depois do ensaio

- Anotar **por cenário**: PASS/FAIL + o comando de evidência + o trecho do transcript. Cenário sem
  evidência colada conta como FAIL, não como "pareceu funcionar".
- Medir o custo real percebido: se o gate atrasar visivelmente cada `Edit`, isso é achado de ensaio
  tanto quanto uma recusa errada (RNF-04).
- **Nomes reciclam:** se uma das sessões for reaberta, o nome pode ser herdado por outra. Ao ler
  transcript ou mensagem, confirmar pelo `messagingSocketPath`/`pid` em `~/.claude/sessions/`, não
  pelo nome.
- Qualquer coisa que exija matar processo: **para o ensaio e pergunta ao Vinicius.** A autorização
  dele vale para aquela limpeza, e não se pede a uma peer.
