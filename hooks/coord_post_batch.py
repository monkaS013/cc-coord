#!/usr/bin/env python
"""coord_post_batch.py - PostToolBatch: ponto barato de aviso agregado.

Entrypoint FINO (T-09). Ver coord_pre_write.py para o essencial das regras 1
(exit 0 sempre), 7 (excecoes cobertas por `hookio.executar` + guarda externa)
e 8 (`CCOORD_SRC` com fallback relativo a este arquivo).

Por que este hook existe (medicao-hooks.md secao 5): `PostToolBatch` injeta
`additionalContext` UMA VEZ por lote de tool calls, em vez de uma vez por
chamada — e o "ponto barato" para o AC-016: em vez de cada `PreToolUse`
individual verificar carimbos de mudanca externa, este hook varre
`<CCOORD_HOME>/changed/` inteiro no fim do lote e reporta o que couber no
orcamento de `additionalContext` de uma vez, e CONSOME (apaga) SO o que foi
de fato reportado — e o que impede o mesmo aviso de se repetir em todo lote
seguinte, sem nunca apagar um carimbo que nao coube na resposta (achado 1 da
auditoria 11/09: a versao anterior apagava TUDO antes de saber quantos
caberiam, perdendo em silencio os que sobrassem do corte de 8000
chars/200 linhas do harness).

Este hook NAO tenta casar os carimbos com os `tool_calls` do payload (o
payload traz `tool_calls: [{tool_name, tool_input, tool_use_id,
tool_response}, ...]` — medicao-hooks.md secao 2): reporta os carimbos
pendentes na maquina (o quanto couber por vez), nao so os relacionados a este
lote. E deliberado — `coord_pre_write.py` ja cobre o caso "pendente para ESTE
arquivo exato" antes de cada Edit/Write; este hook e a rede mais ampla,
best-effort, para o que sobrar (ex.: um Bash ou uma leitura que nao passou
por `coord_pre_write.py` mas ainda assim e um bom momento para avisar). Se
sobrar mais do que cabe numa unica resposta, o resto permanece em disco e
aparece no proximo `PostToolBatch`.
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


def _changed_dir() -> str:
    return os.path.join(_ccoord_home(), "changed")


# Orcamento PROPRIO, abaixo do teto real do harness para `additionalContext`
# (hookio._MAX_CONTEXTO_CHARS/_MAX_CONTEXTO_LINHAS = 8000/200 -- este modulo
# nao importa hookio so para reusar a constante, mesma razao de duplicacao
# dos outros helpers deste arquivo: T-09 nao criou modulo comum para os 7
# entrypoints). Margem deliberada (nao os mesmos 8000/200): garante que ESTE
# hook nunca produza um texto que hookio.emitir_contexto precise truncar --
# ver achado 1 abaixo sobre por que isso importa.
_MAX_CONTEXTO_CHARS = 7800
_MAX_CONTEXTO_LINHAS = 190


def _consumir_carimbos_que_cabem() -> tuple[list[dict], int]:
    """Le TODOS os carimbos pendentes (sem apagar ainda), monta a lista que
    cabe no orcamento de `additionalContext` e SO ENTAO apaga do disco os que
    de fato couberam. Devolve (carimbos_incluidos, quantidade_que_sobrou).

    Achado 1 (auditoria 11/09): a versao anterior lia E APAGAVA (`os.remove`)
    TODO `changed/*.json` ANTES de o texto passar pelo corte de
    hookio.emitir_contexto (8000 chars/200 linhas) -- com muitos carimbos
    pendentes (ex.: `git pull`/rebase de uma peer mexendo em centenas de
    arquivos enquanto esta sessao segue editando), os que nao coubessem no
    corte eram descartados em silencio: apagados do disco, nunca reportados,
    sem aparecer em lugar nenhum (a propria mensagem de truncamento de
    hookio, "ver events.log", e falsa aqui -- este hook nunca escreve nesse
    arquivo). Agora: nenhum carimbo e apagado sem antes ter sido incluido no
    texto que de fato sai nesta chamada. O que nao coube fica intacto em
    `changed/` para o PROXIMO `PostToolBatch`/`PreToolUse` consumir -- nunca
    e destruido so porque a lista ficou grande demais para uma unica
    resposta.
    """
    diretorio = _changed_dir()
    try:
        nomes = sorted(os.listdir(diretorio))
    except OSError:
        return [], 0

    candidatos = []  # (nome_arquivo, dados)
    for nome in nomes:
        if not nome.endswith(".json"):
            continue
        caminho = os.path.join(diretorio, nome)
        try:
            with open(caminho, "r", encoding="utf-8") as fh:
                dados = json.load(fh)
        except Exception:
            continue
        candidatos.append((nome, dados))

    if not candidatos:
        return [], 0

    # 1 linha de cabecalho + 1 linha de rodape sempre presentes (ver
    # _construir_decisor) -- contam no orcamento de linhas junto com cada
    # carimbo incluido.
    linhas_fixas = 2
    incluidos = []
    chars_acumulados = 0
    for nome, dados in candidatos:
        linha = _fmt_carimbo(dados)
        linhas_com_esta = linhas_fixas + len(incluidos) + 1
        chars_com_esta = chars_acumulados + len(linha) + 1  # +1 do \n
        if linhas_com_esta > _MAX_CONTEXTO_LINHAS or chars_com_esta > _MAX_CONTEXTO_CHARS:
            break
        incluidos.append((nome, dados))
        chars_acumulados = chars_com_esta

    pendentes = len(candidatos) - len(incluidos)

    for nome, _dados in incluidos:
        try:
            os.remove(os.path.join(diretorio, nome))
        except OSError:
            pass

    return [d for _n, d in incluidos], pendentes


def _fmt_carimbo(dados: dict) -> str:
    caminho = dados.get("path", "?")
    evento = dados.get("event", "?")
    ts = dados.get("ts")
    if isinstance(ts, (int, float)):
        horario = time.strftime("%H:%M:%S", time.localtime(ts / 1000))
    else:
        horario = "horario desconhecido"
    return f"  - {caminho} ({evento}) as {horario}"


def _construir_decisor():
    from types import SimpleNamespace

    def _decisor(payload: dict):
        carimbos, pendentes = _consumir_carimbos_que_cabem()
        if not carimbos:
            return None

        linhas = [
            "AVISO: arquivo(s) mudaram em disco desde a ultima checagem "
            "(sensor FileChanged nao diz quem alterou):"
        ]
        linhas.extend(_fmt_carimbo(c) for c in carimbos)
        if pendentes:
            # Achado 1: nunca dizer "ver events.log" (mentira -- este hook
            # nao escreve la) nem apagar quem ficou de fora. O que sobrou
            # continua em disco e aparece no proximo PostToolBatch.
            linhas.append(
                f"... e mais {pendentes} alteracao(oes) pendente(s), que "
                "seguem para o proximo PostToolBatch (nao cabiam nesta "
                "resposta)."
            )
        else:
            linhas.append(
                "Confira o conteudo antes de seguir editando; use `ccoord who <arquivo>` "
                "e `SendMessage` se for outra sessao."
            )
        return SimpleNamespace(verdict="warn", reason="\n".join(linhas))

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
