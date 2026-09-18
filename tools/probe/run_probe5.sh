#!/usr/bin/env bash
# Rodada 5 (17/09): responde DUAS perguntas numa sessao so.
#
# T-035 -- experimento PAREADO de encoding: dois deny com a MESMA razao
#   acentuada, um emitido com ensure_ascii=False e outro com True. Quero saber
#   (a) se os dois BLOQUEIAM (JSON invalido nao parseado = gate MUDO) e (b) se
#   a razao chega corrompida no lado False.
# T-030 -- payload real do `UserPromptSubmit` neste build: o campo `source`
#   existe? vem preenchido? o evento dispara dentro de subagente?
#
# Lembrete de metodo (medicao-hooks.md): `--settings` e ADITIVO -- os hooks
# globais do cc-coord rodam junto. Nao da para dizer "so o meu hook rodou".
set -u
BASE="C:/Users/usuario/dev/cc-coord/tools/probe"
cd "$BASE" || exit 1

rm -rf captures5 debug5.log resposta5.txt
mkdir -p captures5
rm -f captures/enc-FALSE.txt captures/enc-TRUE.txt

PROMPT='Execute exatamente estes dois passos, nesta ordem, sem pular nenhum:
Passo 1: use a ferramenta Bash com o comando exatamente: echo ENCFALSE
Passo 2: use a ferramenta Bash com o comando exatamente: echo ENCTRUE
Passo 3: responda APENAS com as duas mensagens de recusa que voce recebeu, uma
por linha, copiadas LITERALMENTE caractere por caractere, sem corrigir nada. Se
alguma das duas nao foi recusada, escreva PASSOU no lugar dela.'

claude -p "$PROMPT" \
  --model haiku \
  --settings "$BASE/probe-settings5.json" \
  --permission-mode bypassPermissions \
  --debug-file "$BASE/debug5.log" \
  > resposta5.txt 2>&1

echo "[probe5] sessao headless terminou"
echo "--- resposta do modelo ---"
cat resposta5.txt
echo "--- bytes que o hook escreveu ---"
cat captures/enc-FALSE.txt 2>/dev/null
cat captures/enc-TRUE.txt 2>/dev/null
echo "--- capturas ---"
ls captures/ 2>/dev/null | grep -i -E "userprompt|subagent" | sed 's/^/  /'
