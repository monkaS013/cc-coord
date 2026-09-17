#!/usr/bin/env bash
# Rodada 6 (17/09): o `UserPromptSubmit` dispara DENTRO de subagente?
#
# Achado A1 da auditoria: se disparar dentro de subagente E o payload nao
# trouxer `agent_id` (que e o estado medido na rodada 5), entao
# `agente_exato=True` compara None == None e o hook apaga o claim do MAIN --
# com o turno do main VIVO, porque um subagente so roda no meio de um turno.
# Seria silencio indevido, o pior modo de falha desta feature.
#
# A pergunta e binaria e mensuravel: capturar `UserPromptSubmit` e
# `SubagentStart` e ver se aparece captura de UserPromptSubmit com `agent_id`,
# ou uma captura a mais durante a vida do subagente.
#
# O capture.py ja nomeia o arquivo com sufixo quando o payload tem `agent_id`.
set -u
BASE="C:/Users/ViniciusMoraisHDT/dev/cc-coord/tools/probe"
cd "$BASE" || exit 1

rm -rf captures6 && mkdir -p captures6
# capture.py grava em captures/ -- limpo so o que e desta pergunta
rm -f captures/UserPromptSubmit-*.json captures/UserPromptSubmit-*.env.json
rm -f captures/SubagentStart-*.json captures/SubagentStart-*.env.json

PROMPT='Use a ferramenta Task (subagente general-purpose) UMA vez, com o prompt
"responda apenas a palavra PRONTO, sem usar ferramenta nenhuma". Depois que ele
responder, responda APENAS com a palavra FIM. Nao use nenhuma outra ferramenta.'

claude -p "$PROMPT" \
  --model haiku \
  --settings "$BASE/probe-settings6.json" \
  --permission-mode bypassPermissions \
  --debug-file "$BASE/debug6.log" \
  > resposta6.txt 2>&1

echo "[probe6] terminou"
echo "--- resposta ---"
tail -3 resposta6.txt
echo "--- capturas de UserPromptSubmit ---"
ls -1 captures/UserPromptSubmit-*.json 2>/dev/null | grep -v env | sed 's/^/  /'
echo "--- capturas de SubagentStart (prova de que o subagente rodou) ---"
ls -1 captures/SubagentStart-*.json 2>/dev/null | grep -v env | sed 's/^/  /'
