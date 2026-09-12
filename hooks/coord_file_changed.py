#!/usr/bin/env python
"""coord_file_changed.py - FileChanged: sensor, nao canal (design.md 3.8).

Entrypoint FINO (T-09). Ver coord_pre_write.py para as regras 1/7/8 comuns.

Medido em medicao-hooks.md secao 1 (Task 1, 11/09): o `FileChanged` existe,
dispara em ~0,6s para alteracao feita por OUTRO processo — mas tambem
dispara igual para a escrita da PROPRIA sessao, e o retorno do hook NUNCA
chega ao modelo (o harness so consome `watchPaths`/`systemMessages` do
retorno; `additionalContext` e descartado, comprovado por ausencia no
transcript JSONL). Por isso:

  1. Este hook NAO tenta avisar o modelo — nem vale a pena tentar, mas
     `hookio.emitir_contexto` ja degradaria para silencio de qualquer jeito
     (FileChanged nao esta em `_EVENTOS_CONTEXTO_PERMITIDO`), entao mesmo um
     `decisor` mal-comportado que devolvesse "warn" aqui sairia calado —
     defesa em profundidade, nao dependemos so deste arquivo se comportar.
  2. FILTRA O ECO (AC-015): o payload de FileChanged e `{file_path, event,
     session_id, ...}` — nao diz QUEM alterou. Este hook compara com o
     carimbo de "escrita propria" que `coord_pre_write.py` deixou em
     `<CCOORD_HOME>/own_writes/` (mesmo path + mesmo session_id do payload,
     dentro de uma janela de tempo). Se casar, e eco: nao registra nada.
  3. Caso contrario, carimba a mudanca em `<CCOORD_HOME>/changed/` com
     arquivo, horario e tipo de evento — e o que `coord_pre_write.py` e
     `coord_post_batch.py` leem depois para avisar o modelo no proximo
     evento com canal de volta (AC-016).

A janela de eco (`_JANELA_ECO_MS`) e generosa (15s) em cima da latencia
medida do watcher (~0,62s, `awaitWriteFinish` de 500ms) para nao falhar por
lentidao de disco/CI — o custo de errar para o lado de "e eco" quando na
verdade nao e, e perder UM aviso; o custo de errar para o outro lado e ficar
avisando a sessao sobre a propria escrita para sempre.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _bootstrap_src_path() -> None:
    src = os.environ.get("CCOORD_SRC")
    if not src or not os.path.isdir(os.path.join(src, "ccoord")):
        # Achado 5 (auditoria 11/09): ver coord_pre_write.py para a
        # justificativa completa -- identica nos 7 entrypoints.
        aqui = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(os.path.dirname(aqui), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _ccoord_home() -> str:
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


def _slug_path(path: str) -> str:
    """Precisa ser IDENTICO ao de coord_pre_write.py/coord_post_batch.py —
    ver o comentario la sobre a duplicacao (restricao da task: sem modulo
    comum entre os 7 entrypoints).
    FONTE UNICA desde 12/09: delega para `ccoord.carimbos.slug_path`. A copia
    local aqui divergiu quando a resolucao de nome curto 8.3 entrou no modulo
    comum -- escritor e leitor do carimbo passaram a usar chaves diferentes, e
    o sintoma (aviso falso de mudanca externa) aponta para o lugar errado.
    """
    try:
        from ccoord.carimbos import slug_path

        return slug_path(path)
    except Exception:  # noqa: BLE001 - fail-open: normalizacao antiga, nunca quebrar o turno
        normalizado = (path or "").strip().replace("\\", "/").lower()
        return "".join(c if (c.isalnum() or c in "-._") else "_" for c in normalizado) or "arquivo"


_JANELA_ECO_MS = 15_000


def _e_eco_da_propria_sessao(file_path: str, session_id: str) -> bool:
    """AC-015: True quando existe um carimbo de escrita propria recente,
    ainda NAO consumido, para este path E dono, deixado por
    `coord_pre_write.py`. Leitura defensiva: qualquer problema (arquivo
    ausente, corrompido) -> "nao e eco" (nunca levanta, nunca esconde uma
    mudanca real por engano de leitura).

    Achado 6 (auditoria 11/09): antes desta correcao, o carimbo ficava
    "valido" pela janela de 15s INTEIRA (`_JANELA_ECO_MS`), entao QUALQUER
    FileChanged com o mesmo (path, session_id) dentro da janela era tratado
    como eco — inclusive uma SEGUNDA mudanca, real, no mesmo arquivo, feita
    por um processo que nunca passa por `coord_pre_write.py` (outra sessao
    via Bash, `git checkout`, Notepad). O payload de FileChanged reporta o
    `session_id` de quem esta OBSERVANDO, nao de quem alterou (docstring do
    modulo) — entao essa segunda mudanca chegava com o MESMO session_id do
    proprio carimbo e era engolida em silencio.

    Correcao: o carimbo so suprime UM FileChanged (o proprio eco da escrita
    que o gerou). Ao casar pela primeira vez, marca `consumido=True` no
    lugar (nao apaga o arquivo): uma segunda ocorrencia dentro da mesma
    janela, com o carimbo ja consumido, deixa de contar como eco e vira
    carimbo de mudanca real em `changed/`. Marcar em vez de apagar (e
    `coord_pre_write.py` sempre reescrevendo do zero a cada edicao nova)
    evita o efeito colateral oposto: duas edicoes legitimas e rapidas da
    MESMA sessao no mesmo arquivo continuam com os dois ecos filtrados,
    porque a segunda escrita da um credito novo (`consumido=False` de novo)
    antes do proximo FileChanged chegar.
    """
    try:
        caminho = os.path.join(_ccoord_home(), "own_writes", _slug_path(file_path) + ".json")
        with open(caminho, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return False

    if dados.get("session_id") != session_id:
        return False
    ts = dados.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    agora_ms = int(time.time() * 1000)
    if not (0 <= (agora_ms - ts) <= _JANELA_ECO_MS):
        return False
    if dados.get("consumido"):
        # ja usamos este carimbo para explicar UM FileChanged -- uma segunda
        # ocorrencia na mesma janela e uma mudanca DE VERDADE, nao o mesmo
        # eco de novo.
        return False

    try:
        dados["consumido"] = True
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, ensure_ascii=False)
    except OSError:
        # marcar e best-effort (RNF-02): se falhar, o pior caso e o proximo
        # FileChanged voltar a ser tratado como eco -- nunca o contrario.
        pass
    return True


def _carimbar_alteracao(file_path: str, evento: str, session_id: str) -> None:
    """Grava `<CCOORD_HOME>/changed/<slug>.json` com arquivo, horario e tipo
    de evento. Best-effort: falha aqui nunca pode quebrar o hook (RNF-02)."""
    try:
        changed_dir = os.path.join(_ccoord_home(), "changed")
        os.makedirs(changed_dir, exist_ok=True)
        caminho = os.path.join(changed_dir, _slug_path(file_path) + ".json")
        dados = {
            "path": file_path,
            "event": evento,
            "ts": int(time.time() * 1000),
            "session_id": session_id,
        }
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, ensure_ascii=False)
    except OSError:
        pass


def _construir_decisor():
    def _decisor(payload: dict):
        file_path = payload.get("file_path")
        if not file_path:
            return None

        evento = payload.get("event") or "change"
        session_id = payload.get("session_id") or ""

        if _e_eco_da_propria_sessao(file_path, session_id):
            return None  # AC-015: eco da propria sessao, nao registra nada

        _carimbar_alteracao(file_path, evento, session_id)
        # FileChanged nao tem canal de volta ao modelo (medido) — nunca ha
        # nada a "avisar" aqui mesmo, so o efeito colateral do carimbo.
        return None

    return _decisor


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio

        payload = hookio.ler_payload()
        decisor = _construir_decisor()
        hookio.executar(payload, decisor)
    except Exception:
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
