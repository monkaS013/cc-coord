#!/usr/bin/env bash
# Rodada 3: (a) FileChanged dispara para escrita da PROPRIA sessao?
#           (b) agent_id aparece no payload dentro de subagente? (AC-009)
#           (c) deny do PreToolUse funciona e a razao chega ao modelo? (AC-011)
set -u
BASE="C:/Users/ViniciusMoraisHDT/dev/cc-coord/tools/probe"
cd "$BASE" || exit 1

rm -rf captures debug3.log resposta3.txt
mkdir -p captures
echo "linha inicial" > alvo.txt

PROMPT='Faca os tres passos, em ordem, sem usar nenhuma outra ferramenta:
1. Use a ferramenta Write para gravar em alvo.txt o texto: escrita pela propria sessao
2. Use a ferramenta Agent (subagent_type general-purpose) com o prompt: execute via Bash o comando echo ola-do-subagente e responda OK
3. Execute via Bash o comando: echo PROIBIDO
No final, responda em 3 linhas curtas: o que aconteceu no passo 3 (texto exato de qualquer recusa que voce recebeu), e liste os marcadores ADDCTX- ou SYSMSG- que voce recebeu.'

claude -p "$PROMPT" \
  --model haiku \
  --settings "$BASE/probe-settings.json" \
  --permission-mode bypassPermissions \
  --debug-file "$BASE/debug3.log" \
  > resposta3.txt 2>&1

echo "[probe] fim"
