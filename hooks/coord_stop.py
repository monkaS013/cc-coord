#!/usr/bin/env python
"""coord_stop.py - Stop: sai CALADO, so libera claims de escopo "turn".

Entrypoint FINO (T-09). Ver coord_pre_write.py para as regras 1/7/8 comuns.

Regra medida (medicao-hooks.md secao 4, AC-013): `additionalContext` num
`Stop` faz a CONVERSA CONTINUAR ("Feedback for the model; the conversation
continues so the model can act on it") — na Rodada 1 da medicao isso gerou
10 disparos de `Stop` em cadeia. Por isso este hook NUNCA emite contexto,
nunca pede deny — a UNICA coisa que faz e liberar os claims de escopo "turn"
desta sessao/agente, e sair calado.

Duas camadas de protecao contra o loop, nao uma so:
  1. Este arquivo passa `lambda p: None` como `decisor` para
     `hookio.executar` — nunca ha um veredito a converter em contexto.
  2. `hookio.executar` TAMBEM trata `Stop` (e `SubagentStop`, `SessionEnd`)
     como eventos de fim de turno/sessao: emite silencio ANTES de sequer
     chamar o `decisor` (ver hookio.py, `_EVENTOS_FIM_DE_TURNO_OU_SESSAO`).
     Mesmo se este arquivo, por bug, passasse um decisor que devolvesse
     "warn", a saida ainda sairia calada.

`stop_hook_active` (presente no payload medido de `Stop`) e a forma de
detectar reentrada: quando True, este `Stop` e ele mesmo o resultado de uma
reentrada anterior — pular o `release()` evita fazer o mesmo trabalho em
cadeia por engano (o release em si e idempotente, mas nao ha motivo para
repeti-lo a cada reentrada).

Achados 3/4 (auditoria 11/09): `<CCOORD_HOME>/own_writes/` (carimbo de
"escrita propria" que `coord_pre_write.py` deixa, um arquivo por PATH
distinto ja tocado por qualquer sessao) nao tinha NENHUM mecanismo de
expiracao/rotacao em todo o modulo — nem TTL, nem `claims.sweep()` (que so
varre `claims/`), nem `ccoord sweep`/`ccoord status` sabiam que o diretorio
existia. Ao longo da vida da instalacao (varios repos/projetos, meses de
uso), isso cresce sem limite. Este hook (fim de turno, fora do caminho
quente por tool-call que T-017 protege) e o ponto natural para varrer: um
carimbo mais velho que a janela de eco (`_JANELA_ECO_MS`, 15s, em
coord_file_changed.py) jamais pode voltar a casar com um `FileChanged`
futuro, entao remove-lo e sempre seguro.
"""

from __future__ import annotations

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


# Bem acima da janela de eco de 15s (`_JANELA_ECO_MS` em
# coord_file_changed.py) -- margem generosa contra relogio de disco lento ou
# um FileChanged atrasado; qualquer carimbo mais velho que isso nunca mais
# pode ser usado por `_e_eco_da_propria_sessao` (que exige `ts` dentro da
# janela), entao remove-lo aqui nunca perde informacao.
_OWN_WRITES_TTL_S = 120


def _varrer_own_writes_expirados(home: str) -> None:
    """Remove `own_writes/*.json` mais velhos que `_OWN_WRITES_TTL_S`
    (achados 3/4). Usa `mtime` do arquivo (nao le/parseia o JSON) -- barato,
    e `_marcar_escrita_propria` sempre reescreve o arquivo inteiro a cada
    escrita, entao `mtime` reflete o `ts` do carimbo. Best-effort total: um
    arquivo que sumiu entre o listdir e o remove, diretorio ausente, ou erro
    de permissao so ficam de fora desta passada -- tenta de novo no proximo
    Stop, nunca propaga."""
    own_dir = os.path.join(home, "own_writes")
    try:
        nomes = os.listdir(own_dir)
    except OSError:
        return
    agora = time.time()
    for nome in nomes:
        if not nome.endswith(".json"):
            continue
        caminho = os.path.join(own_dir, nome)
        try:
            idade_s = agora - os.path.getmtime(caminho)
        except OSError:
            continue
        if idade_s > _OWN_WRITES_TTL_S:
            try:
                os.remove(caminho)
            except OSError:
                pass


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio, claims

        payload = hookio.ler_payload()

        # T-025/AC-021: fim de turno SEMPRE devolve os claims -- mas de dois
        # jeitos diferentes, porque "Stop" nao quer dizer a mesma coisa nos
        # dois casos.
        #
        # O defeito original: o release inteiro ficava sob
        # `if not stop_hook_active`, e isso produziu 1.140 claims presos em 5
        # dias (3.934 tomados contra 2.679 liberados, medido em 17/09). Todo
        # Stop bloqueado por outro hook faz o turno continuar, e nesta maquina
        # o Stop tem QUATRO hooks de terceiros registrados (verify_gate.py,
        # delta_gate.py, obsidian_stop.py, hook.mjs) -- tres medidos
        # bloqueando. Como o ULTIMO Stop de um turno que foi bloqueado alguma
        # vez tambem chega com `stop_hook_active=True`, o turno inteiro
        # terminava sem devolver nada.
        #
        # Mas liberar em reentrada tambem estava errado, e a auditoria
        # adversarial de 17/09 mediu o preco: reentrada NAO e fim de turno, e
        # apagar ali joga fora as faixas ja acumuladas (T-026) -- a peer que
        # editasse exatamente onde eu tinha mexido passava a ouvir "sem
        # sobreposicao". Dai a assimetria abaixo: em reentrada o claim so
        # passa a expirar rapido (a proxima edicao renova e preserva tudo);
        # o release de verdade fica no Stop que nao e reentrada.
        try:
            dono = hookio.identidade(payload)
            if payload.get("stop_hook_active"):
                claims.encurtar_ttl_do_turno(dono, ttl_s=90)
            else:
                claims.release(dono, scope="turn")
        except Exception:
            pass  # ambos ja sao defensivos; guarda extra, nunca propaga

        if not payload.get("stop_hook_active"):
            # Limpeza de manutencao (nao e liberacao de recurso): so uma vez
            # por turno basta, e nada fica preso se ela esperar.
            try:
                _varrer_own_writes_expirados(_ccoord_home())
            except Exception:
                pass  # limpeza best-effort; nunca pode derrubar o turno

        # Nunca ha contexto a devolver aqui - decisor sempre None. Mesmo que
        # houvesse, hookio.executar ja degrada Stop para silencio sozinho.
        hookio.executar(payload, lambda _payload: None)
    except Exception:
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
