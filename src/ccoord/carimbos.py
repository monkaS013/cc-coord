"""ccoord.carimbos - carimbos de escrita propria e de mudanca externa.

FONTE UNICA das tres funcoes que `coord_pre_write.py`, `coord_pre_bash.py`,
`coord_post_batch.py` e `coord_file_changed.py` precisam compartilhar. Elas
existiam duplicadas nos hooks porque a task que os criou (T-09) so podia tocar
em `hooks/` -- duplicacao consciente, registrada em comentario la. O problema de
manter assim ficou concreto em 12/09: ao dar ao `coord_pre_bash.py` o ramo de
escrita de arquivo via Bash, esqueci de carimbar a escrita propria, e o
`FileChanged` seguinte deixou de ser reconhecido como eco -- a sessao passou a
receber "mudou em disco por um processo que nao foi esta sessao" sobre a
propria escrita (achado ALTA da 4a auditoria).

**O acordo que nao pode quebrar:** `slug_path()` tem de produzir EXATAMENTE o
mesmo resultado em quem ESCREVE o carimbo e em quem o LE. Duas implementacoes
que divergem em um unico caractere fazem o eco nunca casar -- e o sintoma
(aviso falso de mudanca externa) nao aponta para a causa. Com uma fonte so,
isso deixa de ser possivel por construcao.

Custo (RNF-04): stdlib apenas (`json`, `os`, `time`), sem import pesado no topo.
"""

from __future__ import annotations

import json
import os
import time

from ccoord.paths import resolver_nome_curto

__all__ = ["home", "slug_path", "marcar_escrita_propria", "consumir_carimbo_pendente"]


def home() -> str:
    """Raiz de estado (`CCOORD_HOME`, default `~/.claude/coord`).

    Igual a `ccoord.claims._home()` de proposito: os hooks precisam do caminho
    sem importar o modulo de claims inteiro no caminho quente.
    """
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


def slug_path(path: str) -> str:
    """Nome de arquivo derivado de um caminho, estavel entre escritor e leitor.

    Passa pela MESMA resolucao de nome curto 8.3 que `classify` usa para montar
    o id do claim (`ccoord.paths`). Sem isso, `VINICI~1\\x.py` e
    `ViniciusMoraisHDT\\x.py` -- o MESMO arquivo -- geram carimbos diferentes,
    o eco da escrita propria nunca casa, e a sessao recebe aviso falso de
    mudanca externa sobre a propria escrita.
    """
    path = resolver_nome_curto(path)
    normalizado = (path or "").strip().replace("\\", "/").lower()
    seguro = "".join(c if (c.isalnum() or c in "-._") else "_" for c in normalizado)
    return seguro or "arquivo"


def marcar_escrita_propria(path: str, session_id: str) -> None:
    """Carimba "esta sessao escreveu neste path agora" (best-effort, nunca levanta).

    `coord_file_changed.py` compara (path, session_id, janela de tempo) com isto
    para decidir se o `FileChanged` recebido e o eco da propria escrita (AC-015)
    ou mudanca externa de verdade (AC-016).

    `consumido: False` explicito: toda escrita NOVA da mesma sessao no mesmo
    path reseta o carimbo, dando credito de eco fresco ao proximo FileChanged
    -- mesmo que o carimbo anterior ja tivesse sido consumido.
    """
    try:
        own_dir = os.path.join(home(), "own_writes")
        os.makedirs(own_dir, exist_ok=True)
        caminho = os.path.join(own_dir, slug_path(path) + ".json")
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "path": path,
                    "session_id": session_id,
                    "ts": int(time.time() * 1000),
                    "consumido": False,
                },
                fh,
                ensure_ascii=False,
            )
    except OSError:
        pass


def consumir_carimbo_pendente(path: str) -> dict | None:
    """Le e APAGA o carimbo de mudanca externa deste path, se houver.

    Consumir e o que evita repetir o mesmo aviso a cada chamada: o AC-016 pede
    "aparece no PROXIMO PreToolUse/PostToolBatch", nao "aparece para sempre".
    """
    caminho = os.path.join(home(), "changed", slug_path(path) + ".json")
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return None
    try:
        os.remove(caminho)
    except OSError:
        pass
    return dados if isinstance(dados, dict) else None
