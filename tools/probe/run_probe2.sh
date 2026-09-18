#!/usr/bin/env bash
# Rodada 2: qual canal (systemMessage x additionalContext) de cada evento chega ao MODELO,
# e o FileChanged disparado por processo externo consegue avisar a sessao?
set -u
BASE="C:/Users/usuario/dev/cc-coord/tools/probe"
cd "$BASE" || exit 1

rm -rf captures debug2.log resposta2.txt
mkdir -p captures
echo "linha inicial" > alvo.txt

PROMPT='Passo 1: execute via Bash exatamente: sleep 18
Passo 2: depois, responda listando TODOS os marcadores que comecem com SYSMSG- ou ADDCTX- que voce tenha recebido em qualquer lugar deste turno (contexto adicional, avisos de sistema, resultado de ferramenta). Um por linha, texto exato. Se nao recebeu nenhum, responda NENHUM. Nao use outras ferramentas.'

claude -p "$PROMPT" \
  --model haiku \
  --settings "$BASE/probe-settings.json" \
  --permission-mode bypassPermissions \
  --debug-file "$BASE/debug2.log" \
  > resposta2.txt 2>&1 &
CLAUDE_PID=$!

sleep 12
T0=$(C:/Python314/python.exe -c "import time;print(int(time.time()*1000))")
echo "ALTERADO POR PROCESSO EXTERNO" >> alvo.txt
echo "[probe] echo externo em t=$T0 ms"

wait $CLAUDE_PID
echo "[probe] fim. t0_echo=$T0"
