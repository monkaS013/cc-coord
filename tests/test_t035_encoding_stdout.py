"""T-035: o JSON que o hook escreve no stdout tem de ser ASCII puro.

Por que este arquivo nao usa `_run_hook` de `test_entrypoints.py`: aquele
helper seta `PYTHONIOENCODING=utf-8` no ambiente do subprocesso, e e
exatamente isso que MASCARA o defeito -- com a variavel setada, o stdout sai
em UTF-8 e `ensure_ascii=False` nunca quebra. O harness NAO seta essa
variavel ao rodar hook (conferido no ambiente real em 17/09), entao a suite
inteira vinha medindo uma condicao que nao e a de producao. E a mesma familia
de "teste e codigo compartilham a suposicao": 335 testes verdes e o defeito
vivo.

O que se afirma aqui sao os BYTES do stdout do subprocesso, nunca a string em
Python -- em Python ela e UTF-8 dos dois jeitos, entao um teste que compare
strings passa com o defeito vivo.

Evidencia de runtime que originou a task (`tools/probe/run_probe5.sh`,
experimento pareado, lido no transcript e nao perguntado ao modelo):
  ensure_ascii=False -> o modelo recebeu "a sess<?>o home est<?> na <?>rea"
  ensure_ascii=True  -> o modelo recebeu "a sessao home esta na Area" integro
Os dois BLOQUEARAM: o harness decodifica com substituicao, entao o `deny` nao
fica mudo. O dano e o aviso chegar ilegivel -- e o caminho de arquivo citado
nele deixar de bater com o arquivo real, que e o que a peer precisa para achar
o que eu toquei.
"""

from __future__ import annotations

try:
    from . import _guarda  # noqa: F401
except ImportError:  # carregado solto
    import _guarda  # noqa: F401

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SRC = RAIZ / "src"
PYTHON = sys.executable

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Caminho REAL desta maquina, nao um acento inventado: o vault e a pasta de
# memoria sao os dois hotspots medidos de colisao, e os dois tem acento.
TEXTO = "peer home editou C:/Oscar Alho/Projetos/Inteligência de Mercado.md — colisão"


def _rodar_sem_pythonioencoding(codigo: str) -> subprocess.CompletedProcess:
    """Roda `codigo` como o HARNESS roda um hook: stdout em pipe, sem
    `PYTHONIOENCODING` no ambiente."""
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [PYTHON, "-c", codigo],
        capture_output=True,  # sem text=: queremos os BYTES
        env=env,
        timeout=30,
    )


class TestEncodingDoStdout(unittest.TestCase):
    def test_imprimir_emite_apenas_ascii(self):
        """@spec:AC-005 o JSON do hook sai em ASCII puro, mesmo com acento na razao"""
        proc = _rodar_sem_pythonioencoding(
            "from ccoord import hookio\n"
            f"hookio.emitir_contexto('PreToolUse', {TEXTO!r})\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        nao_ascii = [b for b in proc.stdout if b > 127]
        self.assertEqual(
            nao_ascii,
            [],
            "stdout do hook tem byte nao-ASCII: com o stdout em cp1252 isso deixa "
            f"de ser UTF-8 valido. Bytes: {proc.stdout[:200]!r}",
        )

    def test_texto_chega_integro_do_outro_lado(self):
        """@spec:AC-005 o acento sobrevive ao round-trip pelo stdout do hook"""
        proc = _rodar_sem_pythonioencoding(
            "from ccoord import hookio\n"
            f"hookio.emitir_contexto('PreToolUse', {TEXTO!r})\n"
        )
        # O consumidor real le UTF-8. Se os bytes forem ASCII, decodificar em
        # UTF-8 e parsear tem de devolver o texto ORIGINAL, acento por acento.
        obj = json.loads(proc.stdout.decode("utf-8"))
        recebido = obj["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Inteligência de Mercado", recebido)
        self.assertIn("colisão", recebido)
        self.assertNotIn("\ufffd", recebido)

    def test_nao_vacuidade_o_ambiente_do_teste_expoe_o_defeito(self):
        """@spec:AC-005 controle: com ensure_ascii=False o mesmo ambiente QUEBRA"""
        # Sem este controle, os dois testes acima passariam mesmo que o
        # ambiente do subprocesso ja fosse UTF-8 -- ou seja, passariam com o
        # conserto revertido, provando nada.
        proc = _rodar_sem_pythonioencoding(
            "import json\n"
            f"print(json.dumps({{'r': {TEXTO!r}}}, ensure_ascii=False))\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        if not any(b > 127 for b in proc.stdout):
            # NAO e falha do codigo: e o ambiente deixando de reproduzir a
            # condicao do harness. Causa conhecida: `PYTHONUTF8=1` ou
            # `-X utf8` (modo UTF-8 do Python, que a PEP 686 torna DEFAULT em
            # versao futura) faz o stdout em pipe ja sair em UTF-8. Quando
            # isso virar o normal desta maquina, o conserto continua correto
            # (ASCII e valido em UTF-8 tambem) e este controle e que perde o
            # sentido -- por isso PULA em vez de reprovar, nomeando a causa.
            self.skipTest(
                "ambiente nao reproduz a condicao do harness: o stdout em pipe "
                f"ja saiu como UTF-8 (PYTHONUTF8={os.environ.get('PYTHONUTF8')!r}, "
                f"sys.flags.utf8_mode={sys.flags.utf8_mode}). Os testes de bytes "
                "deste arquivo continuam valendo, mas este controle nao prova nada aqui."
            )
        with self.assertRaises(
            UnicodeDecodeError,
            msg="esperava bytes invalidos em UTF-8 no lado sem ensure_ascii",
        ):
            proc.stdout.decode("utf-8")


class TestEncodingDoCliJson(unittest.TestCase):
    """O `--json` do CLI tem o MESMO contrato de bytes que o hook.

    Residuo encontrado pela auditoria adversarial sobre o conserto do
    `hookio._imprimir`: o conserto tratou o hook e deixou CINCO `print(
    json.dumps(..., ensure_ascii=False))` no `cli.py`. O `--json` existe
    justamente para consumo programatico (`ccoord status --json | ...`), que e
    onde o stdout E um pipe -- cp1252 nesta maquina.

    Por que `tests/test_cli.py` nunca poderia pegar isto: o helper `_run` de la
    usa `redirect_stdout(io.StringIO())`, e StringIO nao tem encoding -- a
    string em Python e a mesma com `ensure_ascii` True ou False. O teste nao
    estava fraco por descuido; ele era CEGO POR CONSTRUCAO para esta classe de
    defeito. So um subprocesso de verdade, lendo bytes, enxerga.
    """

    def _rodar_cli(self, *args) -> subprocess.CompletedProcess:
        import tempfile

        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env["PYTHONPATH"] = str(SRC)
        # Estado proprio em tempfile: NUNCA `~/.claude/coord`, que e o estado
        # vivo de varias sessoes desta maquina.
        env["CCOORD_HOME"] = self.home
        env["CCOORD_SESSIONS_DIR"] = self.sessions
        return subprocess.run(
            [PYTHON, "-m", "ccoord.cli", *args],
            capture_output=True,  # bytes, nao texto
            env=env,
            timeout=30,
        )

    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="ccoord_t035cli_")
        self.home = os.path.join(self.tmp, "coord")
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.sessions, exist_ok=True)

        # Um claim com acento no caminho, que e o caso REAL (vault e pasta de
        # memoria sao os hotspots medidos de colisao).
        self._antigo = os.environ.get("CCOORD_HOME")
        os.environ["CCOORD_HOME"] = self.home
        from ccoord import claims

        self.path_com_acento = "C:/Oscar Alho/Projetos/Inteligência de Mercado.md"
        dono = claims.Owner(
            session_id="sessao-acentuada",
            pid=os.getpid(),
            proc_start="",
            name="sessão home — Área de Trabalho",
        )
        r = claims.claim(
            "file:" + self.path_com_acento,
            dono,
            ttl_s=900,
            meta={"path": self.path_com_acento, "range": (1, 5), "scope": "turn"},
            esta_vivo=lambda o: True,
        )
        assert r.ok, "fixture nao criou o claim"

    def tearDown(self):
        if self._antigo is None:
            os.environ.pop("CCOORD_HOME", None)
        else:
            os.environ["CCOORD_HOME"] = self._antigo
        # Sem isto cada execucao deixava 3 tempdirs para tras (achado da 3a
        # auditoria) -- e esta suite roda muitas vezes por sessao.
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_formato_do_json_nao_muda_por_descuido(self):
        """@spec:AC-012 o `--json` mantem o formato de cada comando (indent), nao so o encoding"""
        # O helper `_print_json` tem `indent=None` por default, e dois dos cinco
        # pontos de saida passam `indent=2`. A 3a auditoria mediu que trocar
        # `indent=2`->`None` (ou o contrario) sobrevive a suite INTEIRA: nada
        # travava o formato, so o encoding. Quem consome `--json` por linha
        # quebra em silencio com essa troca.
        compacto = self._rodar_cli("sweep", "--json")
        self.assertEqual(compacto.returncode, 0, compacto.stderr[:300])
        self.assertEqual(
            len([l for l in compacto.stdout.split(b"\n") if l.strip()]),
            1,
            "`sweep --json` tem de sair em UMA linha (indent=None): "
            f"{compacto.stdout[:120]!r}",
        )

        indentado = self._rodar_cli("status", "--json")
        self.assertEqual(indentado.returncode, 0, indentado.stderr[:300])
        self.assertGreater(
            len([l for l in indentado.stdout.split(b"\n") if l.strip()]),
            1,
            "`status --json` tem de sair INDENTADO (indent=2), como sempre saiu: "
            f"{indentado.stdout[:120]!r}",
        )

    def test_status_json_sai_em_ascii_puro(self):
        """@spec:AC-012 `status --json` sai em ASCII puro, com acento nos dados"""
        proc = self._rodar_cli("status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        nao_ascii = [b for b in proc.stdout if b > 127]
        self.assertEqual(
            nao_ascii,
            [],
            "`status --json` emitiu byte nao-ASCII: em pipe (cp1252) isso deixa "
            f"de ser UTF-8 valido. Bytes: {proc.stdout[:160]!r}",
        )

    def test_status_json_e_parseavel_como_utf8_e_preserva_acento(self):
        """@spec:AC-012 o consumidor do `--json` le UTF-8 e recupera o acento intacto"""
        proc = self._rodar_cli("status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        # E assim que um consumidor programatico le: decodifica UTF-8 e parseia.
        dados = json.loads(proc.stdout.decode("utf-8"))
        texto = json.dumps(dados, ensure_ascii=False)
        self.assertIn("Inteligência de Mercado", texto)
        self.assertNotIn("\ufffd", texto)

    def test_nao_vacuidade_o_ambiente_do_teste_expoe_o_defeito(self):
        """@spec:AC-012 controle: neste mesmo ambiente, ensure_ascii=False QUEBRA"""
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        proc = subprocess.run(
            [PYTHON, "-c", f"import json;print(json.dumps({{'p': {self.path_com_acento!r}}}, ensure_ascii=False))"],
            capture_output=True,
            env=env,
            timeout=30,
        )
        self.assertTrue(
            any(b > 127 for b in proc.stdout),
            "o ambiente deste teste nao reproduz a condicao real (stdout ja e "
            "UTF-8?) -- entao os dois testes acima nao provam nada",
        )
        with self.assertRaises(UnicodeDecodeError):
            proc.stdout.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
