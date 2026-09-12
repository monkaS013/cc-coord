#!/usr/bin/env bash
# Medicao da Task 1: captura payloads reais de hook e testa se FileChanged dispara
# para alteracao feita por OUTRO processo (proxy de "outra sessao").
set -u
BASE="C:/Users/ViniciusMoraisHDT/dev/cc-coord/tools/probe"
cd "$BASE" || exit 1

rm -rf captures debug.log resposta.txt
mkdir -p captures
echo "linha inicial" > alvo.txt

PROMPT='Passo 1: execute via Bash exatamente o comando: sleep 20
Passo 2: depois que ele terminar, responda APENAS com as linhas que contenham o texto CCOORD-PROBE que voce tenha recebido como contexto adicional neste turno. Se nao recebeu nenhuma, responda exatamente NENHUM. Nao use nenhuma outra ferramenta.'

claude -p "$PROMPT" \
  --model haiku \
  --settings "$BASE/probe-settings.json" \
  --permission-mode bypassPermissions \
  --debug-file "$BASE/debug.log" \
  > resposta.txt 2>&1 &
CLAUDE_PID=$!

# Espera o watcher subir e o turno entrar no sleep, entao altera o arquivo DE FORA.
sleep 12
echo "ALTERADO POR PROCESSO EXTERNO em $(date +%H:%M:%S)" >> alvo.txt
echo "[probe] alvo.txt alterado externamente as $(date +%H:%M:%S)"

wait $CLAUDE_PID
echo "[probe] sessao headless terminou"
ls captures/ 2>/dev/null | sed 's/^/  capture: /'
