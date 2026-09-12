# Resultado do ensaio T-013 — 12/09/2026

Executado depois da instalação real (T-012), contra os hooks em `~/.claude/hooks/`.
Regra do roteiro respeitada: **cenário sem evidência colada conta como FAIL**, e toda evidência sai
de `grep` no transcript JSONL ou do estado em disco — nunca de perguntar à sessão o que ela recebeu.

| # | Cenário | Resultado |
|---|---|---|
| 1 | Mapa de peers no `SessionStart` (AC-012) | **PASS** |
| 2 | Arquivo compartilhado, faixas disjuntas (AC-005) | **PASS** após correção |
| 3 | `Write` sobre arquivo com dono (AC-017) | **PASS** |
| 4 | Kill de processo com claim de peer viva (AC-003/AC-009) | **PASS** |
| 5 | `git commit` com peer viva, citando o hash (AC-007) | **PASS** |
| 6 | Mudança externa + contraprova do eco (AC-016/AC-015) | **PASS** |
| 7 | Fila do recurso (SendMessage + `notify_when_idle`) | **PARCIAL** |
| 8 | Degradação com estado corrompido (AC-010) | **PASS** |

## O que o ensaio achou que 233 testes e 17/17 ACs não acharam

Quatro defeitos, todos no caminho real:

1. **`VINICI~1` e `ViniciusMoraisHDT` geravam ids diferentes para o mesmo arquivo.**
   `os.path.samefile` = True, ids diferentes, gate cego. Como o diretório de scratchpad entregue a
   cada sessão vem no formato 8.3, isso valia para quase toda sessão. Corrigido na T-019.
2. **`cd` em bloco multilinha era invisível.** Três claims criados sobre arquivos fantasma enquanto
   os arquivos reais ficavam sem claim.
3. **`cd "$VAR"` inventava caminho** (`<cwd>\$VAR`).
4. **`>` dentro de string virava redirecionamento** — `python -c "print(a, '->', b)"` criava claim
   sobre um arquivo chamado `, e[`.

E um defeito de política, que virou a T-020: **o gate não separava decisão minha de ordem do
Vinicius**. Ele mandou `taskkill /F /PID 20448` com todas as letras e a razão do deny terminava em
"pergunte ao Vinicius" — pedir autorização a quem deu a ordem.

## Evidência por cenário

**C1 — mapa no SessionStart.** No transcript da sessão B, bloco `attachment`:

```
Sessoes peer vivas:
  - home-indexed-newt (cwd: C:\Users\ViniciusMoraisHDT)
Recursos ocupados:
  - ...\ensaio\compartilhado.py [linhas 5-15] por home-indexed-newt (ensaio cenario 2)
```

**C2 — faixas disjuntas.** Primeira execução: silêncio (o defeito 1 acima). Depois da correção, o
aviso chega como `attachment` e a edição acontece (linha 55 no disco). **A prova mais forte veio
sozinha:** a sessão peer, ao receber o aviso, mandou `SendMessage` negociando antes de editar —
"vou editar a linha 55, fora da sua faixa 5-15, Edit cirúrgico, sem Write. Se você estiver em outra
faixa além de 5-15, me avise."

**C3 — `Write` × `Edit`.** Os dois avisos são distinguíveis, como o AC-017 pede:
- Edit: `AVISO: Edit em ...\app.js colide com as linhas 1-40 ...`
- Write: `AVISO FORTE: Write (reescrita do arquivo inteiro) em ...\app.js — peer-viva tem claim...`

**C4 — kill.** `DENY` com os quatro elementos exigidos (nomeia a peer, dá alternativa concreta,
proíbe o kill, manda perguntar ao Vinicius) e **o processo continuou vivo** — a verificação conta
processo, não lê texto, porque `exit 1` faria a razão aparecer e a ação passar. Funcionou
**inclusive sob `bypassPermissions`**, o que refuta na 2.1.261 o risco levantado pelas issues
#4669/#37210/#43407. Alvo foi um processo dummy criado para o teste, nunca o Chrome do Vinicius.

**C5 — git commit.** `deny` citando o hash curto do commit local que ainda não subiu ao upstream.

**C6 — sensor.** Mudança externa gera 1 carimbo em `changed/`; a escrita da própria sessão gera 0.
As duas metades juntas — sem a contraprova, um sensor que avisasse sobre tudo passaria por acidente.

**C7 — fila do recurso: PARCIAL.** A negociação por `SendMessage` aconteceu **espontaneamente duas
vezes** (C2 e C4), que é o passo difícil. O ciclo completo do §8 da spec — a dona encerra e a
sessão que esperava adquire a lease — não foi executado: exige duas sessões interativas simultâneas.

**C8 — degradação.** Com `CCOORD_HOME` apontando para um arquivo: `Edit` segue (fail-open, exit 0,
silêncio) e `kill` é recusado (fail-closed, exit 0, `deny`).

## Achados de método (custaram tempo, ficam registrados)

- **Fixture sem `pidDomain` faz a peer sumir.** Dois cenários deram FAIL com saída vazia até eu
  perceber que era a fixture, não o produto — o mesmo tropeço que já tinha matado um repro antes.
- **Hook do repo ≠ hook instalado.** Editei `coord_pre_bash.py`, os 256 testes passaram (rodam o do
  repo) e a produção seguia com a cópia antiga. Gate verde sobre código que não está no ar. Agora o
  `SessionStart` avisa quando divergem.
- **O modelo é testemunha não confiável, confirmado de novo.** A sessão B afirmou "nenhum aviso
  apareceu" num caso em que o aviso estava no transcript, e afirmou ter sido bloqueada num caso em
  que o gate nem chegou a rodar.

## Pendente

- Cenário 7 completo (duas sessões interativas).
- Dívidas de desenho documentadas: unidade mapeada × UNC ainda gera ids diferentes; `ccoord restore`
  desfaz o último gesto, não "a última instalação boa"; indentação por tabs no `settings.json` seria
  reformatada na instalação.
