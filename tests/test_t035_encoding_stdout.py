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
        self.assertTrue(
            any(b > 127 for b in proc.stdout),
            "o ambiente deste teste nao reproduz a condicao do harness "
            "(stdout ja e UTF-8?) -- entao os outros dois testes deste arquivo "
            f"nao provam nada. Bytes: {proc.stdout[:200]!r}",
        )
        with self.assertRaises(
            UnicodeDecodeError,
            msg="esperava bytes invalidos em UTF-8 no lado sem ensure_ascii",
        ):
            proc.stdout.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
