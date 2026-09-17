"""T-035: mede o que o harness FAZ com um JSON de hook que nao e UTF-8 valido.

Experimento PAREADO -- os dois lados emitem a MESMA razao acentuada, mudando so
o `ensure_ascii`. Sem o lado de controle nao da para separar "o encoding
corrompeu" de "o pipeline corrompe de qualquer jeito".

  comando contem ENCFALSE -> deny com json.dumps(..., ensure_ascii=False)
  comando contem ENCTRUE  -> deny com json.dumps(..., ensure_ascii=True)

O que se quer saber, em ordem de gravidade:
  1. o deny BLOQUEIA nos dois lados? (se o JSON invalido nao for parseado, o
     gate fica MUDO -- pior modo de falha desta feature)
  2. a razao chega ao modelo corrompida no lado False?

Sai sempre com exit 0: no PreToolUse quem bloqueia e o JSON, e exit != 0/2 faz
a acao PASSAR (medido em 11/09, medicao-hooks.md secao 3).
"""

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
# Acentos + travessao: o travessao foi o caractere que corrompeu no hook da
# peer (byte 0x97 em cp1252). Marcador ASCII no inicio para achar no transcript
# mesmo que o resto vire lixo.
RAZAO = "ENCPROBE {lado}: a sessão home está na Área de Trabalho — colisão de edição"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cmd = str((payload.get("tool_input") or {}).get("command", ""))

    if "ENCFALSE" in cmd:
        lado, ensure = "FALSE", False
    elif "ENCTRUE" in cmd:
        lado, ensure = "TRUE", True
    else:
        return 0  # nao e o experimento: deixa passar, calado

    saida = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": RAZAO.format(lado=lado),
        }
    }
    texto = json.dumps(saida, ensure_ascii=ensure)

    # Registra os BYTES que este processo escreveu, para confrontar com o que o
    # harness entendeu. A string em Python e UTF-8 dos dois jeitos -- o que
    # difere e o que sai no stdout, que aqui e um PIPE (cp1252 nesta maquina).
    try:
        with open(os.path.join(BASE, "captures", f"enc-{lado}.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"ensure_ascii={ensure}\nrepr(texto)={texto!r}\n")
            fh.write(f"stdout.encoding={sys.stdout.encoding}\n")
            fh.write(f"bytes_cp1252={texto.encode(sys.stdout.encoding or 'utf-8', 'replace')!r}\n")
    except OSError:
        pass

    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
