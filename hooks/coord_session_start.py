#!/usr/bin/env python
"""coord_session_start.py - SessionStart: injeta o mapa de coordenacao.

Entrypoint FINO (T-09). Ver coord_pre_write.py para as regras 1/7/8 comuns.

`SessionStart` e um dos eventos com canal CONFIRMADO de `additionalContext`
ao modelo (medicao-hooks.md secao 3). Este hook monta, em texto, o mapa que
o design.md (secao 3.7/RF-06) pede: peers vivas com cwd, recursos ocupados
(claims ativos) e carimbos de alteracao pendentes (arquivos que mudaram em
disco desde o ultimo `coord_file_changed.py`/`coord_post_batch.py` e ainda
nao foram reportados).

Diferenca deliberada para `coord_pre_write.py`/`coord_post_batch.py`: este
hook so LE os carimbos de `<CCOORD_HOME>/changed/`, nunca apaga — sao os
outros dois que "consomem" (apagam) ao reportar de fato ao modelo antes de
uma edicao. Aqui e so contexto de abertura de sessao; apagar um carimbo que
ainda nao gerou um aviso relevante para uma ferramenta especifica seria
perder informacao sem necessidade.

Se nao houver NADA para mostrar (sem peers, sem recurso ocupado, sem
carimbo pendente), devolve `None` — silencio (nao gera ruido todo
`SessionStart` a toa).
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


def _listar_json_dir(nome_subdir: str) -> list[dict]:
    """Le todo `*.json` de `<CCOORD_HOME>/<nome_subdir>/`, defensivo por
    arquivo (mesma postura de claims.py/sessions.py). So para MONTAR o mapa
    de exibicao — nao reimplementa a logica de `claims.py` (que nao expoe
    "listar todos"; mesma justificativa usada em `ccoord/cli.py`, que le o
    schema documentado do diretorio pelo mesmo motivo)."""
    diretorio = os.path.join(_ccoord_home(), nome_subdir)
    try:
        nomes = sorted(os.listdir(diretorio))
    except OSError:
        return []
    itens = []
    for nome in nomes:
        if not nome.endswith(".json"):
            continue
        try:
            with open(os.path.join(diretorio, nome), "r", encoding="utf-8") as fh:
                itens.append(json.load(fh))
        except Exception:
            continue
    return itens


def _fmt_faixa(faixa) -> str:
    if not faixa:
        return "arquivo inteiro"
    try:
        return f"linhas {faixa[0]}-{faixa[1]}"
    except (TypeError, IndexError):
        return "arquivo inteiro"


def _construir_decisor(sessions):
    from types import SimpleNamespace

    def _decisor(payload: dict):
        me = sessions.me(payload.get("session_id"))
        exclude_pid = me.pid if me is not None else None
        peers_vivas = sessions.peers(exclude_pid=exclude_pid)
        peer_ids = {p.session_id for p in peers_vivas if p.session_id}
        meu_session_id = me.session_id if me is not None else payload.get("session_id")

        agora_ms = int(time.time() * 1000)
        claims_ativos = []
        for dados in _listar_json_dir("claims"):
            try:
                renewed_at = int(dados.get("renewed_at", 0))
                ttl_s = int(dados.get("ttl_s", 0))
                if (agora_ms - renewed_at) > (ttl_s * 1000):
                    continue  # expirado - sweep() e quem formaliza a remocao
                owner = dados.get("owner") or {}
                sid = owner.get("session_id")
                # so mostra claim de peer viva ou minha propria - dono morto
                # nao interessa ao mapa (nao bloqueia nada, RNF-02).
                if sid != meu_session_id and sid not in peer_ids:
                    continue
                claims_ativos.append(dados)
            except Exception:
                continue

        carimbos_pendentes = _listar_json_dir("changed")

        if not peers_vivas and not claims_ativos and not carimbos_pendentes:
            return None

        linhas = ["Mapa de coordenacao entre sessoes (coord_session_start):"]

        if peers_vivas:
            linhas.append("Sessoes peer vivas:")
            for p in peers_vivas:
                linhas.append(f"  - {p.name or p.session_id} (cwd: {p.cwd or '?'})")
        else:
            linhas.append("Sessoes peer vivas: nenhuma no momento.")

        if claims_ativos:
            linhas.append("Recursos ocupados:")
            for dados in claims_ativos:
                owner = dados.get("owner") or {}
                dono = owner.get("name") or owner.get("session_id") or "peer desconhecida"
                linhas.append(
                    f"  - {dados.get('path', dados.get('resource', '?'))} "
                    f"[{_fmt_faixa(dados.get('range'))}] por {dono} "
                    f"({dados.get('purpose') or 'sem descricao'})"
                )

        if carimbos_pendentes:
            linhas.append(
                "Alteracoes em disco detectadas e ainda nao confirmadas com o modelo "
                "(sensor FileChanged nao diz quem alterou):"
            )
            for dados in carimbos_pendentes:
                ts = dados.get("ts")
                horario = (
                    time.strftime("%H:%M:%S", time.localtime(ts / 1000))
                    if isinstance(ts, (int, float))
                    else "horario desconhecido"
                )
                linhas.append(
                    f"  - {dados.get('path', '?')} ({dados.get('event', '?')}) as {horario}"
                )

        desatualizados = _entrypoints_desatualizados()
        if desatualizados:
            linhas.append(
                "ATENCAO: estes hooks instalados estao DIFERENTES do repo "
                f"({', '.join(desatualizados)}). O que roda agora e a copia "
                "antiga; a suite de testes exercita a do repo, entao ela pode "
                "estar verde sobre codigo que nao esta no ar. Rode "
                "`ccoord install` para sincronizar."
            )

        return SimpleNamespace(verdict="warn", reason="\n".join(linhas))

    return _decisor


def _entrypoints_desatualizados() -> list:
    """Entrypoints instalados cujo conteudo divergiu do repo.

    Existe por um erro concreto (12/09): editei `coord_pre_bash.py` no repo, os
    256 testes passaram -- eles rodam o arquivo do REPO -- e a producao seguiu
    executando a copia antiga em `~/.claude/hooks/`. Gate verde sobre codigo que
    nao esta no ar; so apareceu porque fui medir o comportamento real. Um teste
    nao pega isso (dependeria da maquina), entao o aviso vive aqui.

    Best-effort: sem `CCOORD_SRC`, sem repo ao lado, ou com erro de leitura,
    devolve lista vazia -- nunca atrapalha o inicio da sessao.
    """
    try:
        import hashlib

        src = os.environ.get("CCOORD_SRC")
        if not src:
            return []
        repo_hooks = os.path.join(os.path.dirname(src.rstrip("\\/")), "hooks")
        instalados = os.path.dirname(os.path.abspath(__file__))
        if os.path.normcase(repo_hooks) == os.path.normcase(instalados):
            return []  # rodando direto do repo: nao ha copia para divergir

        fora = []
        for nome in sorted(os.listdir(instalados)):
            if not nome.startswith("coord_") or not nome.endswith(".py"):
                continue
            origem = os.path.join(repo_hooks, nome)
            if not os.path.isfile(origem):
                continue
            with open(origem, "rb") as a, open(os.path.join(instalados, nome), "rb") as b:
                if hashlib.sha256(a.read()).digest() != hashlib.sha256(b.read()).digest():
                    fora.append(nome)
        return fora
    except Exception:  # noqa: BLE001 - aviso e opcional, inicio de sessao nao
        return []


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio, sessions

        payload = hookio.ler_payload()
        decisor = _construir_decisor(sessions)
        hookio.executar(payload, decisor)
    except Exception:
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
