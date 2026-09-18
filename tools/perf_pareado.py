"""Comparacao PAREADA HEAD x working tree, alternando em blocos curtos.

Medir um lado inteiro e depois o outro nao serve com a maquina sob carga
variavel: a diferenca entre os dois pode ser so a carga que mudou no meio. Aqui
os dois lados sao medidos alternadamente em blocos pequenos, entao qualquer
deriva de carga atinge os dois igualmente. O veredito sai da mediana das
DIFERENCAS por bloco, nao da diferenca das medianas globais.

Uso: python perf_pareado.py <blocos> <execucoes_por_bloco>
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

WT = r"C:\Users\usuario\dev\cc-coord"
HEAD = (
    r"C:\Users\VINICI~1\AppData\Local\Temp\claude\C--Users-usuario"
    r"\731da193-440e-4745-b605-5f08d48c9be4\scratchpad\cc_coord_head"
)
PYTHON = r"C:\Python314\python.exe"

BLOCOS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
POR_BLOCO = int(sys.argv[2]) if len(sys.argv) > 2 else 25


def preparar(repo):
    """Monta o cenario do pior caso e devolve (home, sessions, payload)."""
    sys.path.insert(0, os.path.join(repo, "tests"))
    sys.path.insert(0, os.path.join(repo, "src"))
    for m in list(sys.modules):
        if m.startswith("ccoord") or m == "test_perf":
            del sys.modules[m]
    cwd = os.getcwd()
    os.chdir(repo)
    import test_perf as tp
    from ccoord import classify

    if tp._POOL is None:
        tp.setUpModule()

    class R(tp.TestGateRnf04PiorCasoP95):
        def runTest(self):
            pass

    r = R()
    home, sessions_dir, work = r._preparar(30, 10, prefixo="par")
    alvo = os.path.join(work, "web_app.js")
    old = tp._gerar_arquivo_sintetico(alvo, 8000)

    def _ler(c):
        with open(c, encoding="utf-8") as fh:
            return fh.read()

    rec = classify.classify("Edit", {"file_path": alvo, "old_string": old}, work, ler_arquivo=_ler)[0]
    pid, ticks = tp._POOL.info[0]
    tp._reivindicar_para_peer(home, rec.id, rec.path, rec.lines, pid, ticks, f"peer-{pid}")
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-par",
        "tool_name": "Edit",
        "tool_input": {"file_path": alvo, "old_string": old, "new_string": old + " // x"},
        "cwd": work,
    }
    os.chdir(cwd)
    sys.path.remove(os.path.join(repo, "tests"))
    sys.path.remove(os.path.join(repo, "src"))
    return home, sessions_dir, payload, tp


CENARIOS = {}
for nome, repo in (("HEAD", HEAD), ("WT", WT)):
    CENARIOS[nome] = (repo,) + preparar(repo)


def uma_execucao(repo, home, sessions_dir, payload):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CCOORD_HOME"] = home
    env["CCOORD_SESSIONS_DIR"] = sessions_dir
    env["CCOORD_SRC"] = os.path.join(repo, "src")
    hook = os.path.join(repo, "hooks", "coord_pre_write.py")
    inicio = time.perf_counter()
    subprocess.run(
        [PYTHON, hook],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    return (time.perf_counter() - inicio) * 1000


amostras = {"HEAD": [], "WT": []}
medianas_bloco = {"HEAD": [], "WT": []}

for b in range(BLOCOS):
    for nome in ("HEAD", "WT") if b % 2 == 0 else ("WT", "HEAD"):
        repo, home, sess, payload, _ = CENARIOS[nome]
        t = [uma_execucao(repo, home, sess, payload) for _ in range(POR_BLOCO)]
        amostras[nome].extend(t)
        medianas_bloco[nome].append(statistics.median(t))
    print(
        "bloco %d/%d: HEAD p50=%.1f  WT p50=%.1f  delta=%+.1f ms"
        % (
            b + 1,
            BLOCOS,
            medianas_bloco["HEAD"][-1],
            medianas_bloco["WT"][-1],
            medianas_bloco["WT"][-1] - medianas_bloco["HEAD"][-1],
        )
    )

print()
deltas = [w - h for h, w in zip(medianas_bloco["HEAD"], medianas_bloco["WT"])]
for nome in ("HEAD", "WT"):
    t = sorted(amostras[nome])
    print(
        "%-5s n=%d  p50=%.1f  p90=%.1f  p95=%.1f  max=%.1f"
        % (
            nome,
            len(t),
            statistics.median(t),
            t[int(0.90 * len(t))],
            t[int(0.95 * len(t))],
            t[-1],
        )
    )
print()
print("delta por bloco (WT - HEAD): %s" % ", ".join("%+.1f" % d for d in deltas))
print("mediana dos deltas: %+.1f ms" % statistics.median(deltas))
print("blocos em que WT foi mais lento: %d/%d" % (sum(1 for d in deltas if d > 0), len(deltas)))
