"""Captura o payload bruto de um evento de hook do Claude Code.

Uso: python capture.py <RotuloDoEvento>

Grava o stdin literal em captures/<rotulo>-<ts>.json e devolve uma saida que
testa DOIS canais de comunicacao de volta, com token distinto por evento e canal:
  - systemMessage                         -> SYSMSG-<EVENTO>
  - hookSpecificOutput.additionalContext  -> ADDCTX-<EVENTO>
Assim da para saber, por evento, qual canal o MODELO de fato enxerga.

Stop/SubagentStop nao recebem additionalContext: medido em 11/09, isso faz a
conversa continuar (10 disparos de Stop em loop). SessionEnd rejeita
hookSpecificOutput na validacao do schema.

Em PreToolUse, comando contendo a palavra PROIBIDO vira deny prescritivo --
e o teste do AC-011 (o wrapper hookSpecificOutput e obrigatorio) sem tocar em
nada destrutivo.
"""

import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "captures")
SEM_HOOK_OUTPUT = {"Stop", "SubagentStop", "SessionEnd", "Notification"}

rotulo = sys.argv[1] if len(sys.argv) > 1 else "desconhecido"
raw = sys.stdin.read()

os.makedirs(OUT, exist_ok=True)
ts = time.strftime("%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
sufixo = ""
try:
    payload = json.loads(raw)
except Exception:
    payload = {}
# subagente: nomear o arquivo para nao sobrescrever a captura da thread principal
if payload.get("agent_id"):
    sufixo = "-SUB"

with open(os.path.join(OUT, f"{rotulo}{sufixo}-{ts}.json"), "w", encoding="utf-8") as fh:
    fh.write(raw)

env = {k: v for k, v in os.environ.items() if k.startswith("CLAUDE_")}
with open(os.path.join(OUT, f"{rotulo}{sufixo}-{ts}.env.json"), "w", encoding="utf-8") as fh:
    json.dump(env, fh, indent=2, ensure_ascii=False)

evento = payload.get("hook_event_name", "")

# Teste do deny prescritivo (AC-011), sem alvo destrutivo.
if evento == "PreToolUse" and "PROIBIDO" in json.dumps(payload.get("tool_input", {})):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "DENY-PROBE: recurso em uso pela sessao teste-peer. Alternativa: rode 'echo LIBERADO' em vez deste comando.",
        }
    }, ensure_ascii=False))
    sys.exit(0)

resposta = {"systemMessage": f"SYSMSG-{rotulo}"}
if evento and rotulo not in SEM_HOOK_OUTPUT:
    resposta["hookSpecificOutput"] = {
        "hookEventName": evento,
        "additionalContext": f"ADDCTX-{rotulo}",
    }

print(json.dumps(resposta, ensure_ascii=False))
sys.exit(0)
