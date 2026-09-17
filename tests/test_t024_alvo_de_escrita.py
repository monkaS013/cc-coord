r"""T-024 — o alvo de escrita extraido de um comando precisa ser um ARQUIVO.

Medido no uso real (17/09, 5 dias de `events.log`): **925 de 6.737 ids de
recurso (13,7%) nao eram caminho nenhum** — eram pedacos do proprio comando:
`$STATE_FILE` (variavel de shell nao expandida), `/dev/null)`, `).slice(1);
console.log("probe...`, `open('familia_importacao.py','w').write(...)`. Oito
deles chegaram a `os.open` e voltaram com `[Errno 22] Invalid argument`
(caractere proibido em nome de arquivo no Windows), e DOIS geraram disputa de
claim contra um recurso que nao existe.

Dois defeitos distintos, um teste para cada:

  AC-019 — fragmento de comando nao vira claim. Ruido que ensina a ignorar o
  aviso e como um gate morre: 1 em cada 7 registros era lixo.

  AC-020 — caminho entre ASPAS com espaco vira UM claim, o do arquivo real.
  Este e o mais grave dos dois e nao estava na lista inicial: `_alvos_sed`
  fazia `split()` cru, entao
  `sed -i 's/a/b/' "C:/Users/.../arquivo com espaco.md"` virava TRES claims
  (`...\arquivo`, `...\com`, `...\espaco.md`) e **nenhum do arquivo de
  verdade** — gate cego, nao ruidoso, em toda a familia de caminhos com
  espaco que e a regra nesta maquina ("Area de Trabalho", "Program Files",
  "OneDrive - HDT ENERGY").

Nao-vacuidade: cada bloco tem controle negativo com caminhos legitimos que
PRECISAM continuar virando claim. Um filtro que rejeita demais troca ruido
por cegueira, que e o defeito pior.
"""

from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py).
try:
    from . import _guarda  # noqa: F401
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ccoord import classify as classify_mod  # noqa: E402
from ccoord.classify import classify  # noqa: E402

CWD = r"C:\dev\repo"


def _arquivos(comando, cwd=CWD):
    return [r.path for r in classify("Bash", {"command": comando}, cwd) if r.kind == "file"]


class TestFragmentoDeComandoNaoViraClaim(unittest.TestCase):
    """AC-019 — casos COLHIDOS do events.log real, nao inventados."""

    def test_variavel_de_shell_nao_expandida(self):
        "@spec:AC-019 `> $STATE_FILE` nao vira claim (caso real de 17/09)"
        alvos = _arquivos('STATE_FILE="$HOME/x.txt"; echo oi > $STATE_FILE')
        self.assertEqual(
            alvos, [], f"claim sobre variavel nao expandida: {alvos!r}"
        )

    def test_variavel_estilo_windows(self):
        "@spec:AC-019 `%TEMP%` tambem nao vira claim"
        self.assertEqual(_arquivos("echo oi > %TEMP%\\saida.txt"), [])

    def test_dev_null_com_lixo_colado(self):
        "@spec:AC-019 `/dev/null)` nao vira `C:\\dev\\null)` (caso real de 14/09)"
        alvos = _arquivos("echo teste > /dev/null)")
        self.assertEqual(alvos, [], f"descartavel com sujeira virou claim: {alvos!r}")

    def test_variavel_em_segmento_do_meio(self):
        "@spec:AC-019 `$WORK/t2`, `$SP/arq.py` — variável em qualquer segmento (491 casos no log)"
        for comando in ("echo x > $WORK/t2", "echo x > $SP/mut_corte.py", "echo x > ${TMP}/a.json"):
            with self.subTest(comando=comando):
                self.assertEqual(_arquivos(comando), [], comando)

    def test_cifrao_no_meio_do_NOME_nao_e_variavel(self):
        "@spec:AC-019 `SG$A Rateio.xlsx` e `~$planilha.xlsx` são nomes REAIS, não variáveis"
        # Achado da auditoria de 17/09: casar `$VAR` em qualquer POSIÇÃO cegava
        # planilha de FP&A real e o arquivo de lock do Excel. Só segmento
        # INTEIRO conta como variável.
        for comando, esperado in (
            ('cat b.xlsx > "C:/P&L_2026/SG$A Rateio_Chile.xlsx"', "SG$A Rateio_Chile.xlsx"),
            ('echo x > "C:/dev/~$planilha.xlsx"', "~$planilha.xlsx"),
        ):
            with self.subTest(comando=comando):
                alvos = _arquivos(comando)
                self.assertTrue(alvos, f"o filtro comeu um arquivo real: {comando}")
                self.assertTrue(any(esperado in a for a in alvos), f"{esperado!r} em {alvos!r}")

    def test_caractere_proibido_no_windows(self):
        "@spec:AC-019 alvo com caractere que o Windows proibe em nome de arquivo cai fora"
        # Os 8 `[Errno 22] Invalid argument` do log sairam todos desta classe.
        #
        # `|` e `<` NAO entram nesta lista de proposito: no shell eles sao
        # operadores, entao `echo x > saida|pipe.txt` redireciona mesmo para
        # `saida` e o claim ali esta CERTO. Testa-los aqui seria exigir do
        # filtro um comportamento errado -- os dois casos estavam nesta lista
        # na primeira versao e foram tirados depois de a suite mostrar que o
        # defeito era do teste.
        for comando in (
            'echo x > statefile="c--users-vinicius.txt"',
            "echo x > pergunta?.txt",
            "echo x > glob*.txt",
        ):
            with self.subTest(comando=comando):
                self.assertEqual(_arquivos(comando), [], comando)

    def test_fragmento_que_termina_em_pontuacao_de_codigo(self):
        "@spec:AC-019 fragmento terminado em `;`, `,` ou aspa simples nao vira claim"
        # Ponta a ponta, caso literal do events.log de 15/09:
        self.assertEqual(_arquivos("echo x > , html"), [])
        # Na unidade: `;` e aspa raramente sobrevivem à captura do
        # redirecionamento (o shell separa segmentos antes), então a regra é
        # exercitada direto na função — é ela que os 5 detectores de escrita
        # chamam, e o teste tem de bater onde a decisão acontece.
        for alvo in ("achado,", "saida'", "s.replace(old,new);"):
            with self.subTest(alvo=alvo):
                self.assertFalse(classify_mod._alvo_de_escrita_plausivel(alvo), alvo)
        for alvo in ("Backup (1)", "relatório-2026.md", "C:/dev/x.md"):
            with self.subTest(alvo=alvo):
                self.assertTrue(classify_mod._alvo_de_escrita_plausivel(alvo), alvo)

    def test_fragmento_terminado_em_parentese_PASSA_e_isso_e_deliberado(self):
        "@spec:AC-019 fragmento terminado em `)` passa — preco medido de nao cegar `Backup (1)`"
        # Documenta uma decisão, não um acidente. Rejeitar alvo terminado em
        # `)` pegaria mais 113 fragmentos do log, mas cegaria arquivos reais
        # desta máquina (`Backup (1)`, `css(1)`, `relatorio (copia)`).
        # Cegueira é o defeito pior, então este ruído fica — e fica visível
        # aqui em vez de virar surpresa para quem ler o log depois.
        self.assertTrue(_arquivos("echo x > ).slice(1)"))

    def test_alvo_absurdamente_longo(self):
        "@spec:AC-019 bloco de codigo gigante (>1024) nao vira claim"
        # O teto era 260 (MAX_PATH) e foi medido: não pegava UM fragmento
        # sequer do log e cegava 7.278 arquivos reais de caminho longo.
        self.assertEqual(_arquivos("echo x > " + "a" * 1100), [])

    # ------------------------------------------------------------------
    # Nao-vacuidade: o filtro NAO pode comer caminho legitimo.
    # ------------------------------------------------------------------

    def test_caminhos_legitimos_continuam_virando_claim(self):
        "@spec:AC-019 caminho legitimo (espaco, acento, parenteses, 8.3, UNC) continua virando claim"
        casos = [
            ('printf x > "C:/Program Files (x86)/app/config.ini"', "Program Files (x86)"),
            ('printf x > "C:/Users/Vinicius/relatorio (copia).md"', "relatorio (copia).md"),
            ('printf x > "C:/Users/Vinicius/relatório-2026.md"', "relatório-2026.md"),
            ('printf x > "//servidor/share/dados.txt"', "dados.txt"),
            ("printf x > saida.txt", "saida.txt"),
            ("printf x > ../pai/dados,2026.csv", "dados,2026.csv"),
            # Os quatro abaixo vêm da auditoria de 17/09: a primeira versão do
            # filtro cegava todos eles, e o vault é o alvo de escrita mais
            # frequente desta máquina.
            (
                'echo x > "C:/Oscar Alho/Carreira/Plano - portfolio GitHub '
                '(plano completo, 2026-08-25).md"',
                "(plano completo, 2026-08-25).md",
            ),
            ('echo x > "C:/Users/V/2025.10.14 - R$ 76.519,20 (1).pdf"', "(1).pdf"),
            ('echo x > "C:/Users/V/Backup (1)"', "Backup (1)"),
            ("printf x > " + "a" * 300 + ".md", "a" * 300 + ".md"),
        ]
        for comando, esperado in casos:
            with self.subTest(comando=comando):
                alvos = _arquivos(comando)
                self.assertTrue(alvos, f"o filtro comeu um caminho legitimo: {comando}")
                self.assertTrue(
                    any(esperado in a for a in alvos),
                    f"esperava {esperado!r} em {alvos!r}",
                )


class TestAlvoEntreAspasComEspaco(unittest.TestCase):
    """AC-020 — o gate CEGO: quebrar por espaco perde o arquivo real."""

    def test_sed_inplace_com_caminho_entre_aspas(self):
        "@spec:AC-020 `sed -i` em caminho com espaco gera UM claim, o do arquivo real"
        alvos = _arquivos(
            "sed -i 's/a/b/' \"C:/Users/Vinicius/Area de Trabalho/nota.md\""
        )
        self.assertEqual(len(alvos), 1, f"esperava 1 alvo, veio {alvos!r}")
        self.assertTrue(
            alvos[0].endswith(os.path.join("Area de Trabalho", "nota.md")),
            f"o alvo nao e o arquivo real: {alvos[0]!r}",
        )

    def test_sed_com_varios_arquivos_continua_pegando_todos(self):
        "@spec:AC-020 `sed -i` com varios arquivos segue gerando um claim por arquivo"
        alvos = _arquivos("sed -i 's/a/b/' um.txt dois.txt tres.txt")
        self.assertEqual(len(alvos), 3, f"perdeu alvo: {alvos!r}")

    def test_escape_de_aspa_dentro_de_aspas(self):
        "@spec:AC-020 `sed -i \"s/\\\\\"/X/g\" a.md b.md` nao perde os dois arquivos"
        # Regressão achada pela auditoria: sem tratar `\"` como escape, a aspa
        # fechava cedo e os dois alvos viravam um token só. O `split()` cru
        # acertava este caso — tokenizador novo não pode ser pior.
        alvos = _arquivos('sed -i "s/\\"/X/g" a.md b.md')
        self.assertEqual(len(alvos), 2, f"perdeu alvo com escape: {alvos!r}")

    def test_aspa_sem_par_degrada_para_split(self):
        "@spec:AC-020 aspa sem par nao engole o alvo (degrada para o comportamento antigo)"
        alvos = _arquivos('sed -i "s/a/b/ arquivo.txt')
        self.assertTrue(
            any("arquivo.txt" in a for a in alvos), f"o alvo sumiu com aspa sem par: {alvos!r}"
        )

    def test_tee_com_caminho_entre_aspas(self):
        "@spec:AC-020 `tee` com caminho com espaco tambem vem inteiro"
        alvos = _arquivos('echo x | tee "C:/Oscar Alho/Daily/2026-09-17 (copia).md"')
        self.assertEqual(len(alvos), 1, f"esperava 1 alvo, veio {alvos!r}")
        self.assertTrue(alvos[0].endswith("2026-09-17 (copia).md"), alvos[0])

    def test_copia_com_destino_entre_aspas(self):
        "@spec:AC-020 destino de `cp` com espaco vira o claim do arquivo certo"
        alvos = _arquivos('cp origem.md "C:/Oscar Alho/Daily/2026-09-17.md"')
        self.assertEqual(len(alvos), 1, f"esperava 1 alvo, veio {alvos!r}")
        self.assertTrue(alvos[0].endswith(os.path.join("Daily", "2026-09-17.md")), alvos[0])

    def test_powershell_set_content_com_espaco(self):
        "@spec:AC-020 `-Path \"...com espaco...\"` do PowerShell tambem vem inteiro"
        alvos = _arquivos(
            'Set-Content -Path "C:/Users/Vinicius/OneDrive - HDT ENERGY/x.txt" -Value 1'
        )
        self.assertEqual(len(alvos), 1, f"esperava 1 alvo, veio {alvos!r}")
        self.assertTrue(
            alvos[0].endswith(os.path.join("OneDrive - HDT ENERGY", "x.txt")),
            f"o alvo nao e o arquivo real: {alvos[0]!r}",
        )


if __name__ == "__main__":
    unittest.main()
