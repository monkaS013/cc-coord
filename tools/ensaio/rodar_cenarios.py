"""Cenarios 3, 5, 6 e 8 do roteiro T-013, contra os hooks INSTALADOS.

Nao contra os do repo: o que importa e o que roda em `~/.claude/hooks/`. Estado
(`CCOORD_HOME`, `CCOORD_SESSIONS_DIR`) vai para tempdir, para o ensaio nao sujar
nem depender do estado real da maquina.

Regra do roteiro: cenario sem evidencia colada conta como FAIL. Cada bloco
imprime o que mediu, nao so o veredito.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = "C:/Users/ViniciusMoraisHDT/dev/cc-coord/src"
HOOKS = r"C:\Users\ViniciusMoraisHDT\.claude\hooks"
sys.path.insert(0, SRC)

from ccoord import claims, sessions  # noqa: E402
from ccoord.classify import _path_to_id  # noqa: E402

resultados = []


def registra(cenario, ok, evidencia):
    resultados.append((cenario, ok, evidencia))
    print(f"[{'PASS' if ok else 'FAIL'}] {cenario}")
    print(f"       {evidencia}")
    print()


def roda_hook(nome, payload, home, sessions_dir):
    env = dict(os.environ)
    env["CCOORD_HOME"] = home
    env["CCOORD_SESSIONS_DIR"] = sessions_dir
    env["CCOORD_SRC"] = SRC
    env["PYTHONPATH"] = SRC
    env["CLAUDE_CODE_SESSION_ID"] = payload.get("session_id", "sessao-b")
    env.pop("CLAUDE_PID", None)
    p = subprocess.run(
        [sys.executable, os.path.join(HOOKS, nome)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    saida = (p.stdout or "").strip()
    try:
        return p.returncode, json.loads(saida) if saida else {}
    except Exception:
        return p.returncode, {"__cru__": saida}


def texto_da_decisao(obj):
    hso = obj.get("hookSpecificOutput") or {}
    return (
        hso.get("permissionDecisionReason")
        or hso.get("additionalContext")
        or obj.get("__cru__")
        or ""
    )


def ambiente():
    base = tempfile.mkdtemp(prefix="ensaio_")
    home = os.path.join(base, "coord")
    sessions_dir = os.path.join(base, "sessions")
    os.makedirs(home)
    os.makedirs(sessions_dir)
    os.environ["CCOORD_HOME"] = home
    # peer viva = este processo (PID real, garantidamente vivo agora)
    with open(os.path.join(sessions_dir, f"{os.getpid()}.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "pid": os.getpid(),
                "sessionId": "sessao-peer",
                "cwd": base,
                "procStart": "",
                "status": "busy",
                "updatedAt": int(time.time() * 1000),
                "pidDomain": sessions._local_pid_domain(),
            },
            f,
        )
    return base, home, sessions_dir


# ---------------------------------------------------------------- Cenario 3
base, home, sessions_dir = ambiente()
alvo = os.path.join(base, "app.js")
with open(alvo, "w", encoding="utf-8") as f:
    f.write("\n".join(f"linha {i}" for i in range(1, 41)) + "\n")

rid, path = _path_to_id("file", alvo, "")
dono = claims.Owner(session_id="sessao-peer", pid=os.getpid(), proc_start="", name="peer-viva")
claims.claim(rid, dono, ttl_s=3600, meta={"path": path, "range": (1, 40)})

_, obj_edit = roda_hook(
    "coord_pre_write.py",
    {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-b",
        "cwd": base,
        "tool_name": "Edit",
        "tool_input": {"file_path": alvo, "old_string": "linha 5", "new_string": "x"},
    },
    home,
    sessions_dir,
)
_, obj_write = roda_hook(
    "coord_pre_write.py",
    {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-b",
        "cwd": base,
        "tool_name": "Write",
        "tool_input": {"file_path": alvo, "content": "tudo novo"},
    },
    home,
    sessions_dir,
)
t_edit, t_write = texto_da_decisao(obj_edit), texto_da_decisao(obj_write)
ok3 = bool(t_edit) and bool(t_write) and t_edit != t_write and "peer-viva" in t_write
registra(
    "C3 - Write sobre arquivo com dono recebe aviso DISTINTO do de Edit (AC-017)",
    ok3,
    f"Edit: {t_edit[:110]!r}\n       Write: {t_write[:150]!r}",
)
shutil.rmtree(base, ignore_errors=True)

# ---------------------------------------------------------------- Cenario 5
base, home, sessions_dir = ambiente()
repo = os.path.join(base, "repo")
os.makedirs(repo)


def git(*args, cwd=repo):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, "HOME": base}
    )


git("init", "-q")
git("config", "user.email", "ensaio@local")
git("config", "user.name", "Ensaio")
with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
    f.write("um\n")
git("add", "-A")
git("commit", "-qm", "base")
# upstream local, para haver "commit que ainda nao subiu"
remoto = os.path.join(base, "remoto.git")
subprocess.run(["git", "init", "-q", "--bare", remoto], capture_output=True)
git("remote", "add", "origin", remoto)
git("push", "-q", "-u", "origin", "HEAD")
with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
    f.write("dois\n")
git("add", "-A")
git("commit", "-qm", "trabalho da peer que ainda nao subiu")
hash_local = git("rev-parse", "--short", "HEAD").stdout.strip()

# a peer viva esta NESTE repo
with open(os.path.join(sessions_dir, f"{os.getpid()}.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "pid": os.getpid(),
            "sessionId": "sessao-peer",
            "cwd": repo,
            "procStart": "",
            "status": "busy",
            "updatedAt": int(time.time() * 1000),
            "pidDomain": sessions._local_pid_domain(),
        },
        f,
    )

_, obj_git = roda_hook(
    "coord_pre_bash.py",
    {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-b",
        "cwd": repo,
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "minha alteracao"'},
    },
    home,
    sessions_dir,
)
t_git = texto_da_decisao(obj_git)
decisao_git = (obj_git.get("hookSpecificOutput") or {}).get("permissionDecision")
ok5 = decisao_git == "deny" and hash_local and hash_local in t_git
registra(
    f"C5 - git commit com peer viva e recusado CITANDO o commit local ({hash_local}) (AC-007/T-016)",
    ok5,
    f"decisao={decisao_git} | hash na razao: {hash_local in t_git} | {t_git[:180]!r}",
)
shutil.rmtree(base, ignore_errors=True)

# ---------------------------------------------------------------- Cenario 6
base, home, sessions_dir = ambiente()
alvo = os.path.join(base, "compartilhado.py")
with open(alvo, "w", encoding="utf-8") as f:
    f.write("conteudo\n")

# (a) mudanca EXTERNA: FileChanged de sessao diferente da que escreveu
roda_hook(
    "coord_file_changed.py",
    {
        "hook_event_name": "FileChanged",
        "session_id": "sessao-b",
        "cwd": base,
        "file_path": alvo,
        "event": "modified",
    },
    home,
    sessions_dir,
)
changed = os.path.join(home, "changed")
carimbos_externos = os.listdir(changed) if os.path.isdir(changed) else []

# (b) CONTRAPROVA (AC-015): a propria sessao escreve e o FileChanged seguinte
#     NAO pode virar carimbo -- sem esta metade, um sensor que avisa sobre tudo
#     passaria no item (a) por acidente.
for f_ in list(carimbos_externos):
    os.unlink(os.path.join(changed, f_))
roda_hook(
    "coord_pre_write.py",
    {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-propria",
        "cwd": base,
        "tool_name": "Edit",
        "tool_input": {"file_path": alvo, "old_string": "conteudo", "new_string": "novo"},
    },
    home,
    sessions_dir,
)
roda_hook(
    "coord_file_changed.py",
    {
        "hook_event_name": "FileChanged",
        "session_id": "sessao-propria",
        "cwd": base,
        "file_path": alvo,
        "event": "modified",
    },
    home,
    sessions_dir,
)
carimbos_eco = os.listdir(changed) if os.path.isdir(changed) else []
ok6 = len(carimbos_externos) == 1 and len(carimbos_eco) == 0
registra(
    "C6 - mudanca externa vira carimbo; eco da propria escrita NAO (AC-016 + contraprova AC-015)",
    ok6,
    f"externa -> {len(carimbos_externos)} carimbo(s) {carimbos_externos}; "
    f"eco proprio -> {len(carimbos_eco)} carimbo(s)",
)
shutil.rmtree(base, ignore_errors=True)

# ---------------------------------------------------------------- Cenario 8
base = tempfile.mkdtemp(prefix="ensaio_deg_")
home_quebrado = os.path.join(base, "home_que_e_arquivo")
with open(home_quebrado, "w", encoding="utf-8") as f:
    f.write("isto e um arquivo, nao um diretorio de estado\n")
sessions_dir = os.path.join(base, "sessions")
os.makedirs(sessions_dir)
alvo = os.path.join(base, "x.txt")
with open(alvo, "w", encoding="utf-8") as f:
    f.write("a\n")

rc_edit, obj_edit = roda_hook(
    "coord_pre_write.py",
    {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-b",
        "cwd": base,
        "tool_name": "Edit",
        "tool_input": {"file_path": alvo, "old_string": "a", "new_string": "b"},
    },
    home_quebrado,
    sessions_dir,
)
rc_kill, obj_kill = roda_hook(
    "coord_pre_bash.py",
    {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-b",
        "cwd": base,
        "tool_name": "Bash",
        "tool_input": {"command": "taskkill /IM chrome.exe /F"},
    },
    home_quebrado,
    sessions_dir,
)
d_edit = (obj_edit.get("hookSpecificOutput") or {}).get("permissionDecision")
d_kill = (obj_kill.get("hookSpecificOutput") or {}).get("permissionDecision")
ok8 = rc_edit == 0 and rc_kill == 0 and d_edit != "deny" and d_kill == "deny"
registra(
    "C8 - estado corrompido: Edit segue (fail-open), kill e recusado (fail-closed) (AC-010)",
    ok8,
    f"Edit: rc={rc_edit} decisao={d_edit or '(silencio=allow)'} | "
    f"kill: rc={rc_kill} decisao={d_kill}",
)
shutil.rmtree(base, ignore_errors=True)

print("=" * 70)
falhas = [c for c, ok, _ in resultados if not ok]
print(f"{len(resultados) - len(falhas)}/{len(resultados)} cenarios PASS")
if falhas:
    print("FALHARAM:", falhas)
    raise SystemExit(1)
