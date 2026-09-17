# O que 5 dias de uso real mostraram (medição de 17/09/2026)

A feature entrou em observação em 14/09 ("com uma janela só nada muda; com duas ou mais ele vê o
mapa, os avisos e as recusas"). Esta é a primeira leitura do que o uso produziu — e ela vale mais do
que as cinco auditorias anteriores juntas em um aspecto: **os quatro defeitos abaixo não apareceram
em 300 testes nem em 18 ACs. Apareceram no log.**

Fonte: `~/.claude/coord/events.log`, 6.739 eventos, de 12/09 11:47 a 17/09 12:00.

## O que o uso mostrou de saudável

| | |
|---|---|
| eventos | 6.739 |
| claims tomados / liberados | 3.934 / 2.679 |
| claims recuperados por expiração (`steal_stale`) | 61 |
| disputas (recurso já com dono vivo) | 55 |
| erros | 10 |

Volume diário estável e alto: 2.223 (14/09), 1.621 (15/09), 1.927 (16/09), 704 até meio-dia de 17/09.
35 sessões distintas passaram pelo registro. Os 8 hooks instalados batem byte a byte com o repo.

## Como LER esse log — três erros de leitura cometidos nesta própria medição

1. **`event: "deny"` não é bloqueio de ferramenta.** Ele é emitido por `claims.py` quando a
   *aquisição* falha porque o recurso já tem dono vivo. Quem decide a ferramenta é `policy.py`, e
   para arquivo ela devolve `warn` — nunca `deny`. Contar os "deny" do log como "o gate barrou"
   superestima o atrito em ~100%.
2. **`ts` está em milissegundos.** `datetime.fromtimestamp(ts)` estoura `OSError [Errno 22]` no
   Windows, e dentro de um `try/except` amplo o erro vira rótulo em vez de exceção — some.
3. **Owner e `held_by` com o mesmo `session_id` e `pid` não é bug de identidade**: é a sessão
   disputando consigo mesma entre main e subagente (`agent_id` difere). Eram 6 das 55.

## Defeito 1 — 13,7% dos ids de recurso não eram caminho (→ T-024, AC-019)

925 de 6.737 ids continham fragmento do próprio comando: `$STATE_FILE` (variável não expandida),
`/dev/null)`, `).slice(1); console.log("probe...`, `open('familia.py','w').write(s)`,
`statefile="c--users..."`. **Oito** chegaram a `os.open` e voltaram `[Errno 22] Invalid argument`
(caractere proibido em nome de arquivo no Windows) e **dois** geraram disputa contra um recurso que
não existe.

O dano não é o desperdício: é que 1 em cada 7 registros ser lixo ensina a ignorar o aviso, e é assim
que um gate morre.

## Defeito 2 — caminho com espaço virava três claims errados e nenhum certo (→ T-024, AC-020)

Achado ao escrever o teste do defeito 1, e **mais grave que ele**: `_alvos_sed` fazia `split()` cru,
então

```
sed -i 's/a/b/' "C:/Users/.../Área de Trabalho/nota.md"
```

gerava claims em `...\Área`, `...\de`, `...\Trabalho\nota.md` — **e nenhum no arquivo real**. Gate
cego, não ruidoso, em toda a família de caminho com espaço, que nesta máquina é a regra ("Área de
Trabalho", "Program Files", "OneDrive - HDT ENERGY"). Mesma falha no `-Path` do PowerShell.

## Defeito 3 — 1.140 claims de turno nunca liberados (→ T-025, AC-021/022)

3.934 tomados contra 2.679 liberados, espalhados por **todas** as 35 sessões (~30% do que cada uma
toma). Não é sessão que morre: é sistemático.

Causa, medida no código: o release estava sob `if not payload.get("stop_hook_active")`, para não
repetir trabalho numa reentrada. Só que **numa reentrada os claims não são os mesmos**: todo `Stop`
bloqueado por outro hook faz o turno continuar, e o que for reivindicado nessa continuação só sairia
num `Stop` futuro sem reentrada — ou pelo TTL de 900 s. Nesta máquina o `Stop` tem **quatro** hooks de
terceiros registrados (`verify_gate.py`, `delta_gate.py`, `obsidian_stop.py`, `hook.mjs`); os três
primeiros bloqueiam de fato (medido), do quarto não sei. Um basta: reentrada é rotina, não exceção.

Resultado em disco no dia da medição: 76 claims, **51 com mais de 24 h** — e o mapa do `SessionStart`
mostrava todos como recurso ocupado, ensinando cada sessão nova a respeitar dono que não existe mais.

## Defeito 4 — a faixa do claim congelava na primeira edição do turno (→ T-026, AC-023)

O sintoma que abriu a investigação era "45 das 55 disputas caem na pasta de memória, `MEMORY.md`
sozinho em 18". Ao abrir, o problema não era o volume: era a faixa estar **errada**.

`_renovar()` recriava o claim com `range=existente.range` — a faixa da *primeira* edição do turno —
enquanto o `events.log` registrava a faixa *nova*. O disco e o log discordavam, e foi justamente por
isso que a primeira leitura desta investigação passou perto do defeito sem vê-lo.

Efeito nas duas direções:

- **silêncio indevido** — eu edito `MEMORY.md` nas linhas 10-12 e depois nas 80-84; o claim continua
  dizendo 10-12, e a peer que mexe na 82 ouve *"sem sobreposição, só ciência"*. É exatamente a colisão
  real que a feature existe para pegar;
- **alarme falso** — a peer que mexe na 11, onde eu já não estou, leva aviso forte.

Distribuição medida das 45 disputas em memória: 25 com faixa nula dos dois lados, 13 com dono
faixado e pedido nulo, 5 o inverso, **2 com faixa dos dois lados** (e essas duas eram disjuntas — o
caso em que o aviso "só ciência" está certo).

## O que a auditoria adversarial sobre estas correções achou (mesmo dia)

Dois auditores sobre o diff. **Oito achados, todos corrigidos** — e o mais importante inverte uma
conclusão minha:

**O filtro do defeito 1 trocou ruído por CEGUEIRA.** As regras que escrevi por intuição ("cara de
código": parêntese junto de `;,=`, chamada `\w(`, teto de 260 caracteres) rejeitavam **5,15% dos
arquivos reais desta máquina** — incluindo o vault inteiro, que nomeia nota como
`Plano - portfolio GitHub (plano completo, 2026-08-25).md`, a planilha `SG$A Rateio_Chile.xlsx` e o
arquivo de lock `~$planilha.xlsx` do Excel.

**Por que eu não vi:** usei o próprio `events.log` como controle negativo. Mas o log só contém alvos
que o classificador **já produzia** — ele não podia conter os arquivos que o filtro novo passaria a
rejeitar. Grão errado invalida a medida.

Refeito contra os dois lados (736 fragmentos reais × 129.541 arquivos que existem em disco), com a
regra de aceitação sendo *não cegar nenhum arquivo real*:

| regra | pega fragmentos | cega arquivos reais | |
|---|---|---|---|
| caractere proibido no Windows | 51 | 0 | mantida |
| `:` fora do drive | 63 | 0 | mantida |
| segmento inteiro = `$VAR`/`%VAR%` | 161 | 0 | mantida |
| termina em `;` `,` `'` | 49 | 0 | mantida |
| descartável sujo (`/dev/null)`) | 9 | 0 | mantida |
| parêntese + `;,=` | 53 | **340** | descartada |
| chamada `\w(` | 68 | **622** | descartada |
| `len > 260` | **0** | **7.278** | descartada |

Conjunto final: pega 42% dos fragmentos, cega 0,0000%. O avaliador virou ferramenta versionada
(`tools/avaliar_filtro.py` + `tools/corpus_fragmentos.txt`) porque quem mexer no filtro depois
precisa da tabela dos dois lados, não da intuição de quem escreveu.

**E a correção 3 apagava o que a correção 4 acumulava.** Liberar os claims em reentrada de `Stop`
parecia certo, mas reentrada não é fim de turno — é o turno continuando. As faixas já tocadas sumiam
no meio do trabalho, e a peer que editasse exatamente onde eu estava ouvia "sem sobreposição".
Corrigido com assimetria: reentrada apenas **encurta o TTL para 90 s**; o release de verdade fica no
`Stop` que não é reentrada. Turno que continua renova e preserva tudo; turno que acabou morre em 90 s
em vez de 900.

Outros seis: regressão do tokenizador com aspa escapada e aspa sem par (casos que o `split()` cru
acertava), `tee`/`cp` sem o tokenizador novo, `claims.overlapping()` e `ccoord status/who` lendo só
`range` (dois oráculos para a mesma pergunta), e `ranges` malformado derrubando a decisão em silêncio.

## Custo no caminho quente — o que foi e o que não foi provado

Microbenchmark controlado (mesmo processo, HEAD × corrigido, 3.000 e 1.200 repetições):

| | HEAD | com as correções |
|---|---|---|
| `classify` (4 comandos) p50 | 0,191 ms | 0,353 ms |
| `claim` + renovação p50 | 13,78 ms | 15,61 ms |

Ou seja, ~2 ms de acréscimo contra um orçamento de 150 ms.

**O gate RNF-04 (p95 < 150 ms no pior caso) NÃO foi validado de forma conclusiva.** Medido
alternando os dois fontes na mesma janela, com a máquina sob carga de outra sessão (12 processos
Python de peers): **HEAD reprovou 1 de 4 rodadas, o corrigido reprovou 1 de 4** — o gate oscila em
torno do limite nos dois lados, e o piso do interpretador chegou a 72 ms (o normal é 31 ms). A
conclusão que os dados sustentam é "não há regressão detectável"; a conclusão que eles **não**
sustentam é "o p95 cabe em 150 ms". Remedir com a máquina ociosa antes de afirmar o segundo.

## Dois defeitos medidos que NÃO foram corrigidos

Ambos são anteriores a estas correções e exigem repensar a primitiva de lock — entram como dívida
declarada, não como conserto apressado:

1. **`_renovar` é leitura-modificação-escrita não atômica.** Dois processos renovando o mesmo claim
   ao mesmo tempo: **18 de 21 faixas perdidas**, sem sinal nenhum. A janela não piorou com a T-026
   (10,7% contra 14,6% no HEAD), mas o que se perde passou de uma faixa para até 32.
2. **`_same_owner_identity` é assimétrico entre main e subagente.** Subagente sobre claim do main não
   grava a faixa e a política devolve `allow` (mesmo `session_id`), então uma terceira sessão editando
   ali ouve "sem sobreposição".

## Ponta aberta, declarada

O log conta **disputas**, não **retrabalho evitado**, que é o critério de sucesso do dono. O que dá
para provar hoje: 55 vezes duas sessões quiseram o mesmo recurso e a segunda soube disso antes de
escrever. Quanto trabalho isso salvou, não está instrumentado — e inventar esse número seria pior do
que não ter.
