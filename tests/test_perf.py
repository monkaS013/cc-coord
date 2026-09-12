"""tests/test_perf.py - T-11/T-017: custo do caminho quente dos hooks (RNF-04).

Mede `coord_pre_write.py` e `coord_pre_bash.py` como SUBPROCESSO DE VERDADE
(`subprocess.run` com o payload JSON no stdin) - exatamente como o harness os
chama, e o mesmo padrao ja usado em `tests/test_entrypoints.py`. Uma medicao
que importasse a funcao Python e chamasse direto no processo de teste
esconderia o custo de start do interpretador - e esse custo faz parte do que
o Vinicius sente a cada `Edit`/`Write`/`Bash`, entao ele TEM que entrar na
conta.

Ler antes de mexer:
  - .specs/coordenacao-multissessao/tasks.md T-11
  - .specs/coordenacao-multissessao/design.md secao 7 (Q-005, Q-006) e secao 1
    ("Principio de custo")
  - .specs/coordenacao-multissessao/medicao-perf.md - o RELATORIO da medicao
    original (T-11) e a secao "Otimizacao (T-017)" com os numeros de depois.

**T-017 reorganizou este arquivo** (a suite inteira estava levando >2min por
causa das repeticoes do teste de perf - "rodar a suite" e o gesto mais
frequente do projeto): este arquivo agora so tem o TESTE DE GATE
(`TestGateRnf04PiorCasoP95`, pior caso medido, com N execucoes suficientes
para um p95 honesto) - ele continua rodando em toda chamada de
`tests/run_tap.py`, porque e a unica prova viva de que RNF-04 nao regrediu.
Os outros 11 cenarios (grade de N claims/M sessoes sem conflito, conflito de
porta, Q-005) viraram `tools/bench_hooks.py` - um benchmark exploratorio que
reaproveita toda a infraestrutura abaixo (fixtures, pool, percentis) por
import direto, mas NAO roda como parte da suite (nao fica em `tests/`, nao
segue o padrao `test_*.py` la).

Regra 1 do prompt da task original (T-11): NAO OTIMIZAR NADA aqui - essa regra
valia so para a MEDICAO inicial. T-017 e a task que autoriza otimizar
`src/ccoord/*.py`/`hooks/*.py` a partir do que essa medicao encontrou (custo
fixo de import, nao algoritmo - ver medicao-perf.md secao 5).

Gate de verdade (nao so informativo): `TestGateRnf04PiorCasoP95` falha se o
p95 do cenario mais pesado e realista do caminho quente passar de 150ms. O
cenario escolhido para o gate reproduz o proprio AC-005 (Edit colidindo com
claim de peer viva) sob a carga mais pesada medida (arquivo de ~8000 linhas -
tamanho real do `web/app.js` que motivou esta feature -, 30 claims e 10
sessoes em disco). Se estourar hoje, o teste fica vermelho mesmo assim - o
limite de 150ms e requisito do dono (RNF-04), nao se afrouxa para fazer um
teste passar.

Isolamento: cada cenario usa `CCOORD_HOME`/`CCOORD_SESSIONS_DIR` proprios em
`tempfile`, nunca `~/.claude/coord` real (nem `~/.claude` de forma alguma).
`CCOORD_SRC` aponta para `src/` deste repo, explicito no ambiente do
subprocesso.

Percentis: `_percentil()` usa interpolacao linear entre os postos mais
proximos (o metodo "linear" default do numpy) - documentado aqui porque a
stdlib (RNF-01) nao tem um `percentile()` pronto com essa semantica exata, e
o metodo escolhido muda o numero relatado em amostras pequenas (N=20).

Onde os numeros brutos ficam: cada cenario grava um resumo (p50/p95/p99/
min/max) em `_RESULTADOS` e persiste tudo em JSON no diretorio TEMP do SO
(`CCOORD_PERF_RESULT_PATH`, ou `<tempdir>/ccoord_perf_results.json` por
padrao) - nunca dentro do repo e nunca via `print()` (que corromperia o TAP
que `tests/run_tap.py` produz, lido pelo `onp-spec verify`).
"""


from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"
HOOKS = RAIZ / "hooks"
PYTHON = sys.executable or "python"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ccoord import claims, classify, sessions  # noqa: E402

N_EXECUCOES = 20
LIMITE_P95_MS = 150.0


# ---------------------------------------------------------------------------
# Percentis (stdlib apenas - RNF-01) e persistencia dos resultados brutos
# ---------------------------------------------------------------------------


def _percentil(vals: list, p: float) -> float:
    """Percentil `p` (0-100) por interpolacao linear entre os postos mais
    proximos (metodo "linear" default do numpy). `p=50` e a mediana."""
    if not vals:
        raise ValueError("lista vazia")
    ordenado = sorted(vals)
    if len(ordenado) == 1:
        return ordenado[0]
    posicao = (p / 100.0) * (len(ordenado) - 1)
    piso = int(posicao)
    teto = min(piso + 1, len(ordenado) - 1)
    fracao = posicao - piso
    return ordenado[piso] + (ordenado[teto] - ordenado[piso]) * fracao


_RESULTADOS: list = []


def _resultado_path() -> str:
    return os.environ.get("CCOORD_PERF_RESULT_PATH") or os.path.join(
        tempfile.gettempdir(), "ccoord_perf_results.json"
    )


def _dump_resultados() -> None:
    # best-effort: nunca pode derrubar um teste so por causa da conveniencia
    # de persistir os numeros brutos para o relatorio.
    try:
        destino = _resultado_path()
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        with open(destino, "w", encoding="utf-8") as fh:
            json.dump(_RESULTADOS, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _medir_piso_interpretador(n: int = 7) -> float:
    """p50 de `python -c pass` -- o custo de subir o interpretador, sem codigo nosso.

    Existe porque o limite do RNF-04 e ABSOLUTO e o piso NAO e constante: medido
    em 11-12/09 nesta maquina, oscilou de 31 ms a 69 ms ao longo do dia com a CPU
    igualmente ociosa. Sem medir o piso na mesma rodada, uma reprovacao por
    ambiente e indistinguivel de regressao de codigo -- e as duas aconteceram no
    mesmo dia neste projeto.
    """
    tempos = []
    for _ in range(n):
        inicio = time.perf_counter()
        subprocess.run([PYTHON, "-c", "pass"], capture_output=True)
        tempos.append((time.perf_counter() - inicio) * 1000)
    tempos.sort()
    return round(tempos[len(tempos) // 2], 2)


def _registrar(cenario: str, tempos_ms: list, **meta) -> dict:
    resumo = {
        "cenario": cenario,
        "n": len(tempos_ms),
        "p50_ms": round(_percentil(tempos_ms, 50), 2),
        "p95_ms": round(_percentil(tempos_ms, 95), 2),
        "p99_ms": round(_percentil(tempos_ms, 99), 2),
        "min_ms": round(min(tempos_ms), 2),
        "max_ms": round(max(tempos_ms), 2),
        **meta,
    }
    _RESULTADOS.append(resumo)
    _dump_resultados()  # grava a cada cenario - nao depende de tearDownModule
    return resumo


# ---------------------------------------------------------------------------
# Pool de processos REAIS (sleep) para servir de PID+procStart validos em
# sessoes "vivas de verdade" - mesma tecnica de tests/test_sessions.py
# (subprocess.Popen + sessions._query_process_creation_ticks), so que aqui
# sobem UMA vez para o modulo inteiro (varios cenarios reusam o mesmo pool).
# ---------------------------------------------------------------------------


class _PoolDeProcessosVivos:
    TAMANHO = 10

    def __init__(self) -> None:
        self.procs = [
            subprocess.Popen([PYTHON, "-c", "import time; time.sleep(300)"])
            for _ in range(self.TAMANHO)
        ]
        self.info = []
        for p in self.procs:
            ticks = sessions._query_process_creation_ticks(p.pid)
            self.info.append((p.pid, ticks if isinstance(ticks, int) else None))

    def encerrar(self) -> None:
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except Exception:
                pass


_POOL: "_PoolDeProcessosVivos | None" = None


def setUpModule() -> None:
    global _POOL
    _POOL = _PoolDeProcessosVivos()


def tearDownModule() -> None:
    if _POOL is not None:
        _POOL.encerrar()


# ---------------------------------------------------------------------------
# Fixtures de disco: N claims "de enchimento", claim reivindicada por um peer
# especifico (para o ramo warn/AC-005 e AC-006), M sessoes no registro, e um
# arquivo sintetico com N linhas para o Edit (Q-005).
# ---------------------------------------------------------------------------


@contextmanager
def _ccoord_home_temporario(home: str):
    """`claims.claim()` le `CCOORD_HOME` do ambiente no MOMENTO da chamada -
    troca temporaria para usar a API publica de verdade ao montar fixtures,
    em vez de reimplementar o slug/caminho do arquivo de claim aqui."""
    anterior = os.environ.get("CCOORD_HOME")
    os.environ["CCOORD_HOME"] = home
    try:
        yield
    finally:
        if anterior is None:
            os.environ.pop("CCOORD_HOME", None)
        else:
            os.environ["CCOORD_HOME"] = anterior


def _popular_claims_de_enchimento(home: str, n: int) -> None:
    """N claims de recursos SEM RELACAO com o que o cenario vai editar - so
    para estressar '<CCOORD_HOME>/claims/' com N arquivos em disco, que e a
    dimensao que a task pede medir (3, 10, 30)."""
    with _ccoord_home_temporario(home):
        for i in range(n):
            owner = claims.Owner(
                session_id=f"sessao-enchimento-{i}",
                pid=900_000 + i,
                proc_start="",
                name=f"peer-enchimento-{i}",
            )
            claims.claim(
                f"file:enchimento-{i}",
                owner,
                ttl_s=900,
                meta={
                    "path": f"C:\\dev\\enchimento\\arquivo_{i}.py",
                    "range": [10, 20] if i % 2 == 0 else None,
                    "scope": "turn" if i % 2 == 0 else "session",
                    "purpose": "fixture de carga (T-11) - recurso alheio, nunca tocado pelo hook medido",
                },
            )


def _reivindicar_para_peer(home, resource_id, path, faixa, peer_pid, peer_ticks, peer_name) -> None:
    """Faz um peer (processo real, vivo) reivindicar `resource_id` - e o que
    faz o hook medido cair no ramo `warn` (AC-005/AC-006) em vez de `allow`."""
    owner = claims.Owner(
        session_id=f"sessao-peer-{peer_pid}",
        pid=peer_pid,
        proc_start=str(peer_ticks) if peer_ticks is not None else "",
        name=peer_name,
    )
    with _ccoord_home_temporario(home):
        resultado = claims.claim(
            resource_id,
            owner,
            ttl_s=900,
            meta={
                "path": path,
                "range": list(faixa) if faixa else None,
                "scope": "turn",
                "purpose": "fixture de colisao (T-11) - pior caso realista",
            },
        )
    if not resultado.ok:
        raise AssertionError(f"fixture: peer nao conseguiu reivindicar {resource_id}: {resultado.reason}")


def _escrever_sessao(sessions_dir, pid, ticks, *, session_id, name, cwd="C:\\dev\\algum-repo-peer", status="busy") -> None:
    os.makedirs(sessions_dir, exist_ok=True)
    dados = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": cwd,
        "startedAt": int(time.time() * 1000),
        "procStart": str(ticks) if ticks is not None else "",
        "version": "2.1.261",
        "peerFeatures": [],
        "kind": "interactive",
        "pidDomain": sessions._local_pid_domain(),
        "messagingSocketPath": f"\\\\.\\pipe\\LOCAL\\cc-msg-{pid}",
        "name": name,
        "status": status,
        "updatedAt": int(time.time() * 1000),
    }
    with open(os.path.join(sessions_dir, f"{pid}.json"), "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False)


def _gerar_arquivo_sintetico(caminho: str, n_linhas: int) -> str:
    """Gera um arquivo com `n_linhas` distintas; devolve a linha-alvo (perto
    do FIM do arquivo - pior posicao para uma busca linear de `old_string`
    via `str.find`), sem a quebra de linha final."""
    linhas = [f"const valor_{i} = {i}; // linha sintetica de carga (T-11)\n" for i in range(n_linhas)]
    idx_alvo = max(0, n_linhas - 5)
    alvo = linhas[idx_alvo].rstrip("\n")
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.writelines(linhas)
    return alvo


# ---------------------------------------------------------------------------
# Execucao cronometrada do hook como SUBPROCESSO real (regra 1 do prompt)
# ---------------------------------------------------------------------------


def _run_hook_timed(hook_name: str, payload: dict, *, home: str, sessions_dir: str):
    env = dict(os.environ)
    env.pop("CLAUDE_PID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CCOORD_HOME"] = home
    env["CCOORD_SESSIONS_DIR"] = sessions_dir
    env["CCOORD_SRC"] = str(SRC)

    payload_str = json.dumps(payload)
    inicio = time.perf_counter()
    proc = subprocess.run(
        [PYTHON, str(HOOKS / hook_name)],
        input=payload_str,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    fim = time.perf_counter()
    if proc.returncode != 0:
        raise AssertionError(f"{hook_name} saiu com {proc.returncode}: stdout={proc.stdout!r} stderr={proc.stderr!r}")
    return (fim - inicio) * 1000.0, proc.stdout


# ---------------------------------------------------------------------------
# Base comum dos cenarios
# ---------------------------------------------------------------------------


class _CenarioPerfBase(unittest.TestCase):
    def _preparar(self, n_claims: int, n_sessoes: int, *, prefixo: str = "cenario"):
        self.assertLessEqual(n_sessoes, _POOL.TAMANHO, "pool de processos vivos pequeno demais para o cenario")

        tmp = tempfile.TemporaryDirectory(prefix=f"ccoord_perf_{prefixo}_")
        self.addCleanup(tmp.cleanup)
        home = os.path.join(tmp.name, "home")
        sessions_dir = os.path.join(tmp.name, "sessions")
        work = os.path.join(tmp.name, "work")
        os.makedirs(home, exist_ok=True)
        os.makedirs(sessions_dir, exist_ok=True)
        os.makedirs(work, exist_ok=True)

        _popular_claims_de_enchimento(home, n_claims)
        for pid, ticks in _POOL.info[:n_sessoes]:
            _escrever_sessao(sessions_dir, pid, ticks, session_id=f"sessao-peer-{pid}", name=f"peer-{pid}")

        return home, sessions_dir, work

    def _medir(self, hook_name: str, payload: dict, home: str, sessions_dir: str, n: int = N_EXECUCOES):
        tempos = []
        primeira_saida = None
        for i in range(n):
            elapsed_ms, saida = _run_hook_timed(hook_name, payload, home=home, sessions_dir=sessions_dir)
            tempos.append(elapsed_ms)
            if i == 0:
                primeira_saida = saida
        return tempos, primeira_saida


# ---------------------------------------------------------------------------
# GATE RNF-04: pior caso realista medido nesta suite. Reproduz o proprio
# AC-005 (Edit colidindo com claim de peer viva) sob a carga mais pesada
# (arquivo de 8000 linhas + 30 claims + 10 sessoes em disco). Se estourar
# 150ms, ESTE TESTE FICA VERMELHO - nao e so informativo.
# ---------------------------------------------------------------------------


class TestGateRnf04PiorCasoP95(_CenarioPerfBase):
    def test_gate_p95_hot_path_pior_caso_menor_que_150ms(self):
        "RNF-04 (gate, sem tag de AC de proposito): pior caso do caminho quente (Edit colide com claim de peer viva, arquivo ~8000 linhas, 30 claims e 10 sessoes em disco): p95 < 150ms"
        # Por que SEM `@spec:AC-005` (decidido 11/09, depois de medir o efeito):
        # este teste prova TEMPO, nao COMPORTAMENTO. Com a tag, uma rodada mais
        # lenta por carga da maquina fazia o `onp-spec verify` cair para 16/17 e
        # marcar o AC-005 como sem prova -- ruido de CPU lido como regressao de
        # comportamento. O AC-005 continua provado por
        # tests/test_policy.py::test_edit_com_dono_avisa_nao_bloqueia, que e
        # deterministico. O gate segue valendo: se o p95 estourar 150ms este teste
        # fica VERMELHO e a suite falha -- so nao contamina mais a rastreabilidade
        # de um critetio funcional. O limite nao foi afrouxado.
        home, sessions_dir, work = self._preparar(30, 10, prefixo="gate")
        alvo = os.path.join(work, "web_app.js")
        old_string = _gerar_arquivo_sintetico(alvo, 8000)

        def _ler(caminho):
            with open(caminho, "r", encoding="utf-8") as fh:
                return fh.read()

        # descobre a faixa REAL que classify.py derivaria (mesma funcao que o
        # hook usa) - a claim fixture da peer tem que colidir de verdade, nao
        # um numero de linha chutado a mao.
        recursos = classify.classify("Edit", {"file_path": alvo, "old_string": old_string}, work, ler_arquivo=_ler)
        self.assertEqual(len(recursos), 1)
        recurso = recursos[0]
        self.assertIsNotNone(recurso.lines, "fixture invalida: old_string nao foi localizado no arquivo sintetico")

        pid_peer, ticks_peer = _POOL.info[0]
        nome_peer = f"peer-{pid_peer}"
        _reivindicar_para_peer(home, recurso.id, recurso.path, recurso.lines, pid_peer, ticks_peer, nome_peer)

        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu-gate",
            "tool_name": "Edit",
            "tool_input": {"file_path": alvo, "old_string": old_string, "new_string": old_string + " // editado"},
            "cwd": work,
        }
        tempos, primeira_saida = self._medir("coord_pre_write.py", payload, home, sessions_dir)

        # confirma que o cenario e MESMO o de AC-005 (warn) antes de julgar o
        # numero - senao um "allow" por engano de fixture derrubaria o custo
        # e mentiria sobre o pior caso.
        obj = json.loads(primeira_saida)
        self.assertIn("hookSpecificOutput", obj, f"fixture nao gerou warn (AC-005): {obj!r}")
        aviso = obj["hookSpecificOutput"]["additionalContext"]
        self.assertIn(nome_peer, aviso)

        resumo = _registrar(
            "GATE RNF-04: coord_pre_write.py Edit COM conflito, arquivo 8000 linhas, 30 claims, 10 sessoes (pior caso medido)",
            tempos,
            hook="coord_pre_write.py",
            n_claims=30,
            n_sessoes=10,
            arquivo_linhas=8000,
            conflito=True,
            gate=True,
        )

        # Piso do interpretador medido NA MESMA RODADA. Nao muda o criterio (o
        # limite continua sendo 150 ms absolutos, que e requisito do dono) -- serve
        # para a mensagem de falha dizer QUANTO do tempo e nosso e quanto e do
        # ambiente. Medido em 11-12/09: `python -c pass` nesta maquina oscila entre
        # 31 ms e 69 ms conforme a hora, com a CPU igualmente ociosa; sem este
        # numero ao lado, uma reprovacao por ambiente e indistinguivel de regressao
        # de codigo (aconteceram as duas no mesmo dia).
        # O piso so e medido quando o gate VAI reprovar: a medicao custa ~200ms
        # (7 subprocessos) e, no caminho verde, ninguem le o numero (achado
        # BAIXA da 4a auditoria, 12/09). Por isso a falha e explicita, com
        # `self.fail()`, em vez de `assertLessEqual(..., msg=f"...")` -- a msg
        # de um assert e montada SEMPRE, inclusive quando ele passa, e a
        # primeira versao disto estourou TypeError comparando `None > 45` no
        # caminho verde.
        if resumo["p95_ms"] > LIMITE_P95_MS:
            piso = _medir_piso_interpretador()
            acima_do_piso = round(resumo["p50_ms"] - piso, 2)
            diagnostico = (
                "Se o piso estiver alto (>45ms), suspeite do AMBIENTE antes do codigo: "
                "rode de novo com a maquina ociosa."
                if piso > 45
                else "Piso normal: a reprovacao aponta para o CODIGO."
            )
            self.fail(
                f"RNF-04 violado: p95={resumo['p95_ms']}ms > {LIMITE_P95_MS}ms no pior caso medido "
                f"(p50={resumo['p50_ms']}ms, max={resumo['max_ms']}ms, n={resumo['n']}). "
                f"PISO do interpretador nesta rodada: {piso}ms -- o codigo do hook respondeu por "
                f"~{acima_do_piso}ms acima dele. {diagnostico} "
                "Ver .specs/coordenacao-multissessao/medicao-perf.md."
            )


if __name__ == "__main__":
    unittest.main()
