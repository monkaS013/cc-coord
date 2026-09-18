#!/usr/bin/env bash
# Rodada 4: FileChanged dispara tambem para escrita da PROPRIA sessao?
# (na rodada 3 o Write falhou por exigir Read previo, entao ficou inconclusivo)
set -u
BASE="C:/Users/usuario/dev/cc-coord/tools/probe"
cd "$BASE" || exit 1

rm -rf captures debug4.log resposta4.txt
mkdir -p captures
echo "linha inicial" > alvo.txt

PROMPT='Execute via Bash exatamente este comando e nada mais: echo alterado-pela-propria-sessao >> alvo.txt
Depois responda apenas: FEITO'

claude -p "$PROMPT" \
  --model haiku \
  --settings "$BASE/probe-settings.json" \
  --permission-mode bypassPermissions \
  --debug-file "$BASE/debug4.log" \
  > resposta4.txt 2>&1

echo "[probe] fim"
grep -c "FileChanged: change" debug4.log | sed 's/^/disparos FileChanged no debug: /'
