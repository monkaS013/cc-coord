"""Testes de ccoord.classify (unittest, stdlib — RNF-01, sem pytest).

Tag @spec:AC-xxx na PRIMEIRA LINHA do docstring de cada metodo — e o que
`tests/run_tap.py` usa como titulo TAP, e o que `onp-spec verify` casa com
a spec. Ver .specs/coordenacao-multissessao/spec.md secao 8 (DoD) e
tasks.md T-05.
"""

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401



import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
sys.path.insert(0, SRC)

from ccoord.classify import classify  # noqa: E402


class TestClassify(unittest.TestCase):
    # ------------------------------------------------------------------
    # AC-004 — worktree nao pode ser confundido com o checkout primario
    # ------------------------------------------------------------------
    def test_worktree_nao_confundido_com_checkout_primario(self):
        "@spec:AC-004 worktree nao e confundido com o checkout primario"
        cwd_checkout_primario = r"C:\Users\ViniciusMoraisHDT\dev\workday-hdt-ts"
        file_path_worktree = (
            r"C:\Users\ViniciusMoraisHDT\dev\workday-hdt-ts"
            r"\.claude\worktrees\feature-x\src\app.ts"
        )

        recursos = classify(
            "Write",
            {"file_path": file_path_worktree, "content": "conteudo novo"},
            cwd_checkout_primario,
        )

        self.assertEqual(len(recursos), 1)
        recurso = recursos[0]
        self.assertEqual(recurso.kind, "file")
        self.assertEqual(recurso.action, "write")
        self.assertIsNone(recurso.lines)
        # O id tem que vir do file_path (dentro do worktree), nunca do cwd.
        self.assertIn("worktrees", recurso.id)
        self.assertIn("feature-x", recurso.id)
        self.assertNotEqual(
            recurso.id,
            classify(
                "Write",
                {"file_path": cwd_checkout_primario + r"\app.ts", "content": "x"},
                cwd_checkout_primario,
            )[0].id,
        )

    def test_edit_tambem_chaveia_no_file_path_no_cwd(self):
        "@spec:AC-004 Edit dentro de worktree tambem chaveia no file_path"
        cwd_checkout_primario = r"C:\Users\ViniciusMoraisHDT\dev\workday-hdt-ts"
        file_path_worktree = (
            r"C:\Users\ViniciusMoraisHDT\dev\workday-hdt-ts"
            r"\.claude\worktrees\feature-x\src\app.ts"
        )
        recursos = classify(
            "Edit",
            {
                "file_path": file_path_worktree,
                "old_string": "algo",
                "new_string": "outro algo",
            },
            cwd_checkout_primario,
        )
        self.assertEqual(len(recursos), 1)
        self.assertEqual(recursos[0].path, file_path_worktree)

    # ------------------------------------------------------------------
    # AC-003 — kill de processo de perfil de browser
    # ------------------------------------------------------------------
    def test_kill_de_processo_de_perfil_de_browser(self):
        "@spec:AC-003 kill de processo de perfil de browser e reconhecido"
        recursos = classify(
            "Bash",
            {"command": "Stop-Process -Name chrome -Force"},
            r"C:\Users\ViniciusMoraisHDT\dev\algum-projeto",
        )
        self.assertEqual(len(recursos), 1)
        recurso = recursos[0]
        self.assertEqual(recurso.action, "kill")
        self.assertEqual(recurso.kind, "browser")
        self.assertEqual(recurso.id, "browser:chrome")

    def test_kill_via_taskkill_im(self):
        "@spec:AC-003 taskkill /IM tambem e reconhecido como kill de browser"
        recursos = classify(
            "Bash",
            {"command": 'taskkill /F /IM "msedge.exe"'},
            r"C:\dev\x",
        )
        self.assertEqual(len(recursos), 1)
        self.assertEqual(recursos[0].kind, "browser")
        self.assertEqual(recursos[0].action, "kill")
        self.assertIn("msedge", recursos[0].id)

    def test_kill_de_processo_generico_nao_e_browser(self):
        "@spec:AC-003 kill de processo que nao casa perfil de browser vira process"
        recursos = classify(
            "Bash",
            {"command": "kill 4242"},
            r"C:\dev\x",
        )
        self.assertEqual(len(recursos), 1)
        self.assertEqual(recursos[0].kind, "process")
        self.assertEqual(recursos[0].action, "kill")
        self.assertEqual(recursos[0].id, "process:4242")

    # ------------------------------------------------------------------
    # AC-006 — bind de porta
    # ------------------------------------------------------------------
    def test_bind_de_porta_extrai_numero(self):
        "@spec:AC-006 bind de porta e reconhecido com o numero extraido"
        recursos = classify(
            "Bash",
            {"command": "uvicorn app:app --host 0.0.0.0 --port 8099"},
            r"C:\Users\ViniciusMoraisHDT\dev\dashboard-im",
        )
        portas = [r for r in recursos if r.kind == "port"]
        self.assertEqual(len(portas), 1)
        self.assertEqual(portas[0].id, "port:8099")
        self.assertEqual(portas[0].action, "bind")

    def test_bind_de_porta_flag_curta_e_env(self):
        "@spec:AC-006 -p N e PORT=N tambem contam como bind"
        r1 = classify("Bash", {"command": "next dev -p 3100"}, r"C:\dev\x")
        r2 = classify("Bash", {"command": "PORT=5000 node server.js"}, r"C:\dev\x")
        self.assertTrue(any(r.id == "port:3100" for r in r1))
        self.assertTrue(any(r.id == "port:5000" for r in r2))

    # ------------------------------------------------------------------
    # AC-007 — git commit e git push reconhecidos, repo identificado
    # ------------------------------------------------------------------
    def test_git_commit_e_push_reconhecidos_mesmo_repo(self):
        "@spec:AC-007 git commit e git push sao reconhecidos com o repo identificado"
        cwd = r"C:\Users\ViniciusMoraisHDT\dev\workday-hdt-ts"
        recursos = classify(
            "Bash",
            {"command": 'git commit -m "wip" && git push origin main'},
            cwd,
        )
        acoes = {r.action for r in recursos}
        self.assertIn("commit", acoes)
        self.assertIn("push", acoes)
        ids = {r.id for r in recursos}
        self.assertEqual(len(ids), 1)  # mesmo repo -> mesmo id
        self.assertTrue(all(r.kind == "git" for r in recursos))

    def test_git_reset_e_checkout_tambem_contam_como_git_write(self):
        "@spec:AC-007 git reset e git checkout tambem sao git write"
        cwd = r"C:\dev\repo-x"
        r1 = classify("Bash", {"command": "git reset --hard HEAD~1"}, cwd)
        r2 = classify("Bash", {"command": "git checkout main"}, cwd)
        self.assertTrue(any(r.action == "reset" for r in r1))
        self.assertTrue(any(r.action == "checkout" for r in r2))

    def test_git_flags_globais_entre_git_e_o_verbo_nao_escondem_commit_push(self):
        "@spec:AC-007 --no-pager/--git-dir=/--work-tree= entre git e o verbo nao escondem commit/push"
        # Achado #5 (3a auditoria, ALTA): o regex original so tolerava
        # exatamente `-C <path>` entre "git" e o verbo -- qualquer OUTRA flag
        # global quebrava a adjacencia e classify() devolvia lista vazia,
        # desarmando o deny de commit/push com peer viva.
        cwd = r"C:\dev\repo-x"
        r1 = classify("Bash", {"command": 'git --no-pager commit -m "x"'}, cwd)
        r2 = classify("Bash", {"command": "git --no-pager push origin main"}, cwd)
        r3 = classify(
            "Bash", {"command": 'git --git-dir=.git --work-tree=. commit -m "x"'}, cwd
        )
        self.assertTrue(any(r.action == "commit" for r in r1))
        self.assertTrue(any(r.action == "push" for r in r2))
        self.assertTrue(any(r.action == "commit" for r in r3))
        # o repo tem que continuar sendo identificado certo (mesmo id que sem flags)
        r_base = classify("Bash", {"command": 'git commit -m "x"'}, cwd)
        ids1 = {r.id for r in r1 if r.action == "commit"}
        ids_base = {r.id for r in r_base if r.action == "commit"}
        self.assertEqual(ids1, ids_base)

    def test_git_c_flag_continua_funcionando_com_outras_flags_antes(self):
        "@spec:AC-007 -C <path> ainda e reconhecido mesmo com outra flag global antes dele"
        r = classify(
            "Bash",
            {"command": r'git --no-pager -C C:\dev\outro-repo commit -m "x"'},
            r"C:\dev\repo-x",
        )
        commits = [x for x in r if x.action == "commit"]
        self.assertEqual(len(commits), 1)
        self.assertIn("outro-repo", commits[0].id)

    def test_cd_encadeado_antes_de_git_commit_usa_o_repo_de_destino(self):
        "@spec:AC-007 cd encadeado (cd X && git commit) classifica no repo de destino, nao no cwd original"
        # Achado #3 (3a auditoria, ALTA): `cd <repo> && git commit` dentro do
        # MESMO comando Bash classificava pelo cwd do payload (onde a sessao
        # estava), nunca pelo diretorio onde o commit de fato roda -- o
        # commit acontecia no repo da peer sem deny nenhum.
        cwd_original = r"C:\dev\repo-errado"
        repo_real = r"C:\dev\repo-real"

        r_encadeado = classify(
            "Bash", {"command": f'cd {repo_real} && git commit -m "x"'}, cwd_original
        )
        r_direto_no_real = classify("Bash", {"command": 'git commit -m "x"'}, repo_real)
        r_direto_no_errado = classify(
            "Bash", {"command": 'git commit -m "x"'}, cwd_original
        )

        ids_encadeado = {r.id for r in r_encadeado if r.action == "commit"}
        ids_real = {r.id for r in r_direto_no_real if r.action == "commit"}
        ids_errado = {r.id for r in r_direto_no_errado if r.action == "commit"}

        self.assertTrue(ids_encadeado)
        self.assertEqual(ids_encadeado, ids_real)
        self.assertNotEqual(ids_encadeado, ids_errado)

    def test_cd_encadeado_tambem_vale_pra_bind_e_migracao(self):
        "@spec:AC-006 cd encadeado tambem corrige o repo usado por bind/migracao"
        cwd_original = r"C:\dev\repo-errado"
        repo_real = r"C:\dev\workday-hdt-ts"
        r = classify(
            "Bash",
            {"command": f"cd {repo_real} && npx prisma migrate dev"},
            cwd_original,
        )
        db = [x for x in r if x.kind == "db"]
        self.assertEqual(len(db), 1)
        self.assertIn("workday-hdt-ts", db[0].id)

    def test_migracao_de_schema_prisma_e_alembic(self):
        "@spec:AC-007 migracao de schema (prisma migrate / alembic) e reconhecida"
        cwd = r"C:\Users\ViniciusMoraisHDT\dev\workday-hdt-ts"
        r1 = classify("Bash", {"command": "npx prisma migrate dev"}, cwd)
        r2 = classify("Bash", {"command": "alembic upgrade head"}, cwd)
        self.assertTrue(any(r.kind == "db" and r.action == "migrate" for r in r1))
        self.assertTrue(any(r.kind == "db" and r.action == "migrate" for r in r2))

    # ------------------------------------------------------------------
    # Faixa de linha derivada do old_string (com ler_arquivo injetado)
    # ------------------------------------------------------------------
    def test_faixa_de_linha_derivada_do_old_string(self):
        "@spec:AC-Edit faixa de linha e derivada do old_string via ler_arquivo"
        conteudo = "linha1\nlinha2\nold start\nold middle\nold end\nlinha6\n"
        old_string = "old start\nold middle\nold end"

        def ler_arquivo(path):
            return conteudo

        recursos = classify(
            "Edit",
            {
                "file_path": r"C:\dev\x\arquivo.py",
                "old_string": old_string,
                "new_string": "novo conteudo",
            },
            r"C:\dev\x",
            ler_arquivo=ler_arquivo,
        )
        self.assertEqual(len(recursos), 1)
        self.assertEqual(recursos[0].lines, (3, 5))
        self.assertEqual(recursos[0].action, "edit")

    def test_faixa_de_linha_degrada_sem_ler_arquivo(self):
        "@spec:AC-Edit sem ler_arquivo degrada para lines=None (arquivo inteiro)"
        recursos = classify(
            "Edit",
            {
                "file_path": r"C:\dev\x\arquivo.py",
                "old_string": "algo",
                "new_string": "outro",
            },
            r"C:\dev\x",
        )
        self.assertEqual(len(recursos), 1)
        self.assertIsNone(recursos[0].lines)

    def test_faixa_de_linha_degrada_quando_old_string_nao_encontrado(self):
        "@spec:AC-Edit old_string nao localizado degrada para lines=None"

        def ler_arquivo(path):
            return "conteudo completamente diferente\nsem relacao\n"

        recursos = classify(
            "Edit",
            {
                "file_path": r"C:\dev\x\arquivo.py",
                "old_string": "trecho que nao existe no arquivo",
                "new_string": "novo",
            },
            r"C:\dev\x",
            ler_arquivo=ler_arquivo,
        )
        self.assertEqual(len(recursos), 1)
        self.assertIsNone(recursos[0].lines)

    def test_write_e_sempre_arquivo_inteiro(self):
        "@spec:AC-Write Write sempre reivindica o arquivo inteiro (lines=None)"
        recursos = classify(
            "Write",
            {"file_path": r"C:\dev\x\arquivo.py", "content": "tudo novo"},
            r"C:\dev\x",
        )
        self.assertEqual(len(recursos), 1)
        self.assertIsNone(recursos[0].lines)
        self.assertEqual(recursos[0].action, "write")

    # ------------------------------------------------------------------
    # Normalizacao de caminho (regra 5)
    # ------------------------------------------------------------------
    def test_normalizacao_de_caminho_mesmo_arquivo_duas_grafias(self):
        "@spec:AC-Norm mesmo arquivo escrito de duas formas gera o mesmo id"
        r1 = classify(
            "Write",
            {"file_path": r"C:\Users\ViniciusMoraisHDT\dev\App\Index.JS", "content": "a"},
            r"C:\dev\x",
        )
        r2 = classify(
            "Write",
            {"file_path": "c:/users/viniciusmoraishdt/dev/app/index.js", "content": "b"},
            r"C:\dev\x",
        )
        self.assertEqual(r1[0].id, r2[0].id)

    def test_normalizacao_resolve_ponto_ponto(self):
        "@spec:AC-Norm caminho com .. e resolvido antes de virar id"
        r1 = classify(
            "Write",
            {
                "file_path": r"C:\Users\Vinicius\dev\projeto-a\..\projeto-b\app.js",
                "content": "a",
            },
            r"C:\dev\x",
        )
        r2 = classify(
            "Write",
            {"file_path": r"C:\Users\Vinicius\dev\projeto-b\app.js", "content": "b"},
            r"C:\dev\x",
        )
        self.assertEqual(r1[0].id, r2[0].id)

    def test_unc_nao_colide_com_raiz_relativa_da_unidade(self):
        "@spec:AC-Norm UNC de rede (2 barras) nunca colide com raiz-relativa (1 barra)"
        # Achado #1 (3a auditoria, MEDIA): o codigo original descartava a
        # CONTAGEM de barras iniciais -- \\server\share\x (UNC de rede) e
        # \server\share\x (raiz-relativa de unidade) sao dois enderecamentos
        # CONCEITUALMENTE diferentes que colapsavam no mesmo id.
        r_unc = classify(
            "Write", {"file_path": r"\\server\share\file.txt", "content": "a"}, ""
        )
        r_raiz = classify(
            "Write", {"file_path": r"\server\share\file.txt", "content": "a"}, ""
        )
        self.assertNotEqual(r_unc[0].id, r_raiz[0].id)

    def test_caminho_relativo_junta_com_cwd_diferente_por_projeto(self):
        "@spec:AC-Norm file_path relativo (sem drive/barra) junta com o cwd, nao descarta"
        # Achado #1 (3a auditoria, MEDIA): sem isso, o MESMO nome relativo em
        # dois projetos diferentes (cwd diferente) colapsava no mesmo id --
        # dois arquivos REAIS diferentes viravam indistinguiveis.
        rA = classify(
            "Write",
            {"file_path": r"notas\rascunho.md", "content": "a"},
            r"C:\Users\Vinicius\ProjetoA",
        )
        rB = classify(
            "Write",
            {"file_path": r"notas\rascunho.md", "content": "b"},
            r"C:\Users\Vinicius\ProjetoB",
        )
        self.assertNotEqual(rA[0].id, rB[0].id)
        self.assertIn("projetoa", rA[0].id)
        self.assertIn("projetob", rB[0].id)

    def test_prefixo_de_caminho_longo_do_windows_colapsa_no_mesmo_id(self):
        "@spec:AC-Norm prefixo \\\\?\\ (caminho longo) colapsa no mesmo id do caminho sem prefixo"
        # Achado #2 (3a auditoria, MEDIA), parte corrigivel: \\?\ e \\?\UNC\
        # sao so marcador de sintaxe do Windows p/ ignorar MAX_PATH -- ferra-
        # mentas adicionam sozinhas para paths >260 chars. Manipulacao pura
        # de string (sem IO): o MESMO arquivo nao pode virar dois ids so pela
        # presenca do prefixo.
        r1 = classify(
            "Write", {"file_path": r"C:\pastalonga\arquivo.py", "content": "a"}, r"C:\dev\x"
        )
        r2 = classify(
            "Write",
            {"file_path": r"\\?\C:\pastalonga\arquivo.py", "content": "a"},
            r"C:\dev\x",
        )
        self.assertEqual(r1[0].id, r2[0].id)

        r3 = classify(
            "Write", {"file_path": r"\\server\share\arquivo.py", "content": "a"}, r"C:\dev\x"
        )
        r4 = classify(
            "Write",
            {"file_path": r"\\?\UNC\server\share\arquivo.py", "content": "a"},
            r"C:\dev\x",
        )
        self.assertEqual(r3[0].id, r4[0].id)

    def test_unidade_mapeada_vs_unc_e_limitacao_conhecida_sem_io(self):
        "@spec:AC-Norm unidade mapeada e UNC do mesmo recurso de rede NAO colapsam (limitacao aceita, sem IO)"
        # Achado #2 (3a auditoria, MEDIA), parte NAO corrigivel dentro deste
        # modulo: resolver "Z: mapeada para \\server\share" exigiria consultar
        # o SO (qual UNC uma letra de unidade resolve agora), o que
        # `_normalize_path` explicitamente evita (deterministico, sem IO,
        # RNF-04). Este teste documenta a decisao CONSCIENTE (o achado
        # apontava que antes ninguem tinha registrado isso em ASM-xxx algum):
        # os dois seguem com ids DIFERENTES ate que outro modulo (com IO)
        # resolva a equivalencia.
        r_mapeada = classify(
            "Write", {"file_path": r"Z:\project\app.js", "content": "a"}, r"C:\dev\x"
        )
        r_unc = classify(
            "Write",
            {"file_path": r"\\server\share\project\app.js", "content": "a"},
            r"C:\dev\x",
        )
        self.assertNotEqual(r_mapeada[0].id, r_unc[0].id)

    # ------------------------------------------------------------------
    # Degradacao geral / nao quebra
    # ------------------------------------------------------------------
    def test_ferramenta_desconhecida_devolve_lista_vazia(self):
        "@spec:AC-Degrad ferramenta desconhecida nao quebra, devolve lista vazia"
        self.assertEqual(classify("ToolQueNaoExiste", {"x": 1}, r"C:\dev\x"), [])

    def test_bash_sem_command_devolve_lista_vazia(self):
        "@spec:AC-Degrad Bash sem command devolve lista vazia"
        self.assertEqual(classify("Bash", {}, r"C:\dev\x"), [])

    def test_bash_sem_gatilho_nenhum_devolve_lista_vazia(self):
        "@spec:AC-Degrad Bash comum sem kill/git/porta/migracao nao gera recurso"
        self.assertEqual(
            classify("Bash", {"command": "ls -la"}, r"C:\dev\x"), []
        )


if __name__ == "__main__":
    unittest.main()


class TestBypassesDeKillReproduzidosPelaAuditoria(unittest.TestCase):
    """Achado ALTA da 3a auditoria (11/09): 4 formas de matar processo passavam
    pelo gate SEM ser reconhecidas -- `wmic ... call terminate`, `wmic ...
    delete`, `pskill` (a fronteira de palavra falhava com "kill" colado ao "s")
    e `spps` (alias nativo do PowerShell para Stop-Process). Todas foram
    reproduzidas contra um claim vivo e receberam `allow`.

    Gate cego e pior que gate ausente: promete proteger e libera em silencio.
    """

    ATAQUES = [
        "taskkill /IM chrome.exe /F",
        "taskkill /PID 12345 /F",
        "wmic process where name=chrome.exe call terminate",
        "wmic process where name=chrome.exe delete",
        "pskill chrome.exe",
        "tskill 1234",
        "spps -Name chrome",
        "Get-Process chrome | Stop-Process -Force",
        "Stop-Process -Id 12345 -Force",
        "pkill -f chrome",
    ]

    # O contrapeso: o dono tem uma pasta `skills/` cheia de arquivos, e um
    # curinga tipo `\w*kill\b` casaria "skill". Falso positivo aqui vira recusa
    # de comando inocente -- exatamente o atrito que a feature existe para evitar.
    INOCENTES = [
        "echo ola",
        "git status",
        "python skills/killer_app.py",
        "cd ~/.claude/skills && ls",
        "cat skill.md",
        "npm run build",
        "grep -r kill_switch src/",
    ]

    def test_todas_as_formas_de_kill_sao_reconhecidas(self):
        "@spec:AC-003 wmic/pskill/tskill/spps e as demais formas de kill viram recurso de processo"
        for comando in self.ATAQUES:
            with self.subTest(comando=comando):
                recursos = classify("Bash", {"command": comando}, "C:/tmp")
                tipos = [r.kind for r in recursos]
                self.assertTrue(
                    any(t in ("browser", "process") for t in tipos),
                    f"bypass do gate: {comando!r} nao foi reconhecido como kill (tipos={tipos})",
                )

    def test_comandos_inocentes_nao_viram_kill(self):
        "@spec:AC-003 comando inocente com 'skill'/'kill_switch' no texto nao vira kill (nao-vacuidade)"
        for comando in self.INOCENTES:
            with self.subTest(comando=comando):
                tipos = [r.kind for r in classify("Bash", {"command": comando}, "C:/tmp")]
                self.assertFalse(
                    any(t in ("browser", "process") for t in tipos),
                    f"falso positivo: {comando!r} foi classificado como kill (tipos={tipos})",
                )

    def test_curinga_no_alvo_do_kill_expande_para_grafias_reais_de_browser(self):
        "@spec:AC-003 curinga (* ou ?) no alvo do kill expande para as grafias reais do browser conhecido"
        # Achado #6 (3a auditoria, ALTA): `taskkill /F /IM chrom*` e
        # `Stop-Process -Name chrom* -Force` miram o MESMO chrome.exe de uma
        # peer viva, mas o id original virava literal ('process:chrom*'),
        # que nunca bate com o claim real (`claims.owner_of()` faz
        # correspondencia EXATA). Expande para as grafias reais mais comuns
        # (com e sem `.exe`) do conjunto FECHADO de browsers conhecidos.
        for comando in ("taskkill /F /IM chrom*", "Stop-Process -Name chrom* -Force"):
            with self.subTest(comando=comando):
                recursos = classify("Bash", {"command": comando}, r"C:\dev\x")
                ids = {r.id for r in recursos}
                self.assertIn("browser:chrome", ids)
                self.assertIn("browser:chrome.exe", ids)
                self.assertTrue(all(r.kind == "browser" and r.action == "kill" for r in recursos))

    def test_alvo_exato_do_kill_continua_preservando_a_grafia_literal(self):
        "@spec:AC-003 sem curinga, o id do kill continua sendo a grafia literal do comando (nao regride)"
        # Sem curinga, NAO normalizamos para a forma canonica -- isso
        # preservaria o casamento exato hoje relied-upon contra um claim
        # gravado com a MESMA grafia literal do kill (ex.: 'browser:chrome.exe').
        recursos = classify("Bash", {"command": "taskkill /IM chrome.exe /F"}, r"C:\dev\x")
        self.assertEqual(len(recursos), 1)
        self.assertEqual(recursos[0].id, "browser:chrome.exe")


class TestEscritaDeArquivoViaBash(unittest.TestCase):
    """Achado #4 (3a auditoria, ALTA): `_classify_bash` nunca detectava
    escrita de arquivo -- redirecionamento (`>`/`>>`), `sed -i`, cmdlets do
    PowerShell (`Set-Content`/`Add-Content`/`Out-File`) passavam batido:
    `classify()` devolvia lista vazia e nenhum claim/policy era consultado.
    Reproduzido ponta a ponta pela auditoria contra um claim vivo real nas
    3 formas -- todas com stdout `{}` (allow em silencio).
    """

    def test_redirecionamento_e_classificado_como_escrita_do_arquivo(self):
        "@spec:AC-BashWrite echo/redirecionamento (> e >>) em Bash e classificado como write do arquivo alvo"
        cwd = r"C:\dev\x"
        r_write_tool = classify(
            "Write", {"file_path": r"C:\dev\x\app.js", "content": "y"}, cwd
        )
        r_bash = classify(
            "Bash", {"command": 'echo conteudo-do-atacante > "app.js"'}, cwd
        )
        alvo = [r for r in r_bash if r.kind == "file" and r.action == "write"]
        self.assertEqual(len(alvo), 1)
        self.assertEqual(alvo[0].id, r_write_tool[0].id)

    def test_sed_inplace_e_classificado_como_escrita_do_arquivo(self):
        "@spec:AC-BashWrite sed -i e classificado como write do arquivo alvo"
        cwd = r"C:\dev\x"
        r_write_tool = classify(
            "Write", {"file_path": r"C:\dev\x\app.js", "content": "y"}, cwd
        )
        r_bash = classify(
            "Bash", {"command": 'sed -i "s/original/hackeado/" app.js'}, cwd
        )
        alvo = [r for r in r_bash if r.kind == "file" and r.action == "write"]
        self.assertEqual(len(alvo), 1)
        self.assertEqual(alvo[0].id, r_write_tool[0].id)

    def test_set_content_powershell_e_classificado_como_escrita_do_arquivo(self):
        "@spec:AC-BashWrite Set-Content -Path do PowerShell e classificado como write do arquivo alvo"
        cwd = r"C:\dev\x"
        r_write_tool = classify(
            "Write", {"file_path": r"C:\dev\x\app.js", "content": "y"}, cwd
        )
        r_bash = classify(
            "Bash", {"command": 'Set-Content -Path "app.js" -Value hackeado'}, cwd
        )
        alvo = [r for r in r_bash if r.kind == "file" and r.action == "write"]
        self.assertEqual(len(alvo), 1)
        self.assertEqual(alvo[0].id, r_write_tool[0].id)

    def test_out_file_e_add_content_tambem_sao_reconhecidos(self):
        "@spec:AC-BashWrite Out-File e Add-Content tambem sao reconhecidos como write"
        cwd = r"C:\dev\x"
        r1 = classify("Bash", {"command": '"conteudo" | Out-File -FilePath app.js'}, cwd)
        r2 = classify("Bash", {"command": "Add-Content -Path app.js -Value mais"}, cwd)
        for r in (r1, r2):
            alvo = [x for x in r if x.kind == "file" and x.action == "write"]
            self.assertEqual(len(alvo), 1)

    def test_tee_e_classificado_como_escrita_do_arquivo(self):
        "@spec:AC-BashWrite tee e classificado como write do arquivo alvo"
        cwd = r"C:\dev\x"
        r_bash = classify("Bash", {"command": "echo x | tee app.js"}, cwd)
        alvo = [r for r in r_bash if r.kind == "file" and r.action == "write"]
        self.assertEqual(len(alvo), 1)
        self.assertIn("app.js", alvo[0].path.lower())

    def test_redirecionamento_relativo_resolve_contra_o_cwd_efetivo(self):
        "@spec:AC-BashWrite alvo relativo do redirecionamento resolve contra o cwd (cd encadeado incluso)"
        cwd_original = r"C:\dev\repo-errado"
        repo_real = r"C:\dev\repo-real"
        r_encadeado = classify(
            "Bash", {"command": f'cd {repo_real} && echo x > app.js'}, cwd_original
        )
        r_direto = classify("Bash", {"command": "echo x > app.js"}, repo_real)
        alvo_encadeado = [r for r in r_encadeado if r.kind == "file"]
        alvo_direto = [r for r in r_direto if r.kind == "file"]
        self.assertEqual(len(alvo_encadeado), 1)
        self.assertEqual(alvo_encadeado[0].id, alvo_direto[0].id)

    def test_redirecionamento_de_descritor_nao_vira_escrita_de_arquivo(self):
        "@spec:AC-BashWrite redirecionamento de descritor (2>&1, >&2) nao vira write de arquivo (nao-vacuidade)"
        for comando in ("comando 2>&1", "comando >&2", "comando 2>/dev/null"):
            with self.subTest(comando=comando):
                recursos = classify("Bash", {"command": comando}, r"C:\dev\x")
                self.assertFalse(any(r.kind == "file" for r in recursos))


class TestMultiplosArquivosNumComando(unittest.TestCase):
    """Achado ALTA da 4a auditoria (12/09): comando que escreve em VARIOS
    arquivos protegia so um deles -- `sed -i` so o ultimo, `tee` so o
    primeiro, e `Set-Content -Path a,b` gerava um id com virgula dentro que
    nao protegia nenhum. Claim em um arquivo e silencio nos demais e pior que
    nao ter gate: passa impressao de cobertura.
    """

    def _arquivos(self, comando):
        return [r for r in classify("Bash", {"command": comando}, "C:/p") if r.kind == "file"]

    def test_sed_inplace_protege_todos_os_alvos(self):
        "@spec:AC-005 sed -i com varios arquivos gera um recurso para CADA um"
        rs = self._arquivos('sed -i "s/a/b/" f1.txt f2.txt f3.txt')
        nomes = sorted((r.path or r.id).replace("\\", "/").split("/")[-1] for r in rs)
        self.assertEqual(nomes, ["f1.txt", "f2.txt", "f3.txt"])

    def test_tee_protege_todos_os_alvos(self):
        "@spec:AC-005 tee com varios arquivos gera um recurso para CADA um"
        rs = self._arquivos("tee f1.txt f2.txt")
        nomes = sorted((r.path or r.id).replace("\\", "/").split("/")[-1] for r in rs)
        self.assertEqual(nomes, ["f1.txt", "f2.txt"])

    def test_set_content_com_lista_protege_cada_arquivo(self):
        "@spec:AC-005 Set-Content -Path a,b separa a lista em vez de virar um id com virgula"
        rs = self._arquivos("Set-Content -Path f1.txt,f2.txt -Value x")
        nomes = sorted((r.path or r.id).replace("\\", "/").split("/")[-1] for r in rs)
        self.assertEqual(nomes, ["f1.txt", "f2.txt"])
        for r in rs:
            self.assertNotIn(",", r.id, "id de recurso nunca pode conter a lista inteira")

    def test_flags_e_expressao_nao_viram_arquivo(self):
        "@spec:AC-005 flags, expressao do sed e comando sem alvo nao viram recurso de arquivo (nao-vacuidade)"
        self.assertEqual(len(self._arquivos('sed -i -e "s/a/b/" f1.txt')), 1)
        self.assertEqual(len(self._arquivos("tee -a log.txt")), 1)
        for inocente in ("echo ola", "git status", "sed --version", "ls -la"):
            with self.subTest(comando=inocente):
                self.assertEqual(self._arquivos(inocente), [])


class TestCopiaEMoveComoEscrita(unittest.TestCase):
    """`cp`/`copy`/`Copy-Item`/`move`/`Rename-Item` eram a ultima forma
    conhecida de ESCREVER num arquivo com claim de peer viva sem o gate
    perceber -- divida registrada pela 3a auditoria (12/09), fechada aqui.
    O recurso e sempre o DESTINO; a origem e leitura.
    """

    def _arquivos(self, comando):
        return [r for r in classify("Bash", {"command": comando}, "C:/p") if r.kind == "file"]

    def _nomes(self, comando):
        return [(r.path or r.id).replace("\\", "/").split("/")[-1] for r in self._arquivos(comando)]

    def test_destino_da_copia_vira_recurso(self):
        "@spec:AC-005 cp/copy/Copy-Item/move/Rename-Item classificam o DESTINO como escrita"
        for comando, destino in (
            ("cp origem.txt destino.txt", "destino.txt"),
            ("copy a.js b.js", "b.js"),
            ("Copy-Item x.md y.md", "y.md"),
            ("move velho.txt novo.txt", "novo.txt"),
            ("Rename-Item a.txt b.txt", "b.txt"),
            ("Copy-Item -Path a.txt -Destination b.txt", "b.txt"),
        ):
            with self.subTest(comando=comando):
                self.assertIn(destino, self._nomes(comando))

    def test_origem_nao_vira_recurso(self):
        "a origem da copia e leitura: nao pode gerar claim (senao avisa sobre arquivo que so foi lido)"
        nomes = self._nomes("cp origem.txt destino.txt")
        self.assertNotIn("origem.txt", nomes)

    def test_git_mv_tambem_e_escrita(self):
        "git mv escreve no destino de verdade -- classificar como escrita e correto, nao falso positivo"
        self.assertIn("b.txt", self._nomes("git mv a.txt b.txt"))

    def test_casos_que_nao_podem_virar_escrita(self):
        "@spec:AC-005 copia sem destino de arquivo, ou palavra solta, nao vira claim (nao-vacuidade)"
        for inocente in ("cp -r pasta/", "echo copy isso", "ls", "Copy-Item -Recurse pasta/"):
            with self.subTest(comando=inocente):
                self.assertEqual(self._arquivos(inocente), [])
