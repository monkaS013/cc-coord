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

import ctypes  # noqa: E402

from ccoord.classify import classify  # noqa: E402
from ccoord.classify import _resolver_nome_curto  # noqa: E402


class TestClassify(unittest.TestCase):
    # ------------------------------------------------------------------
    # AC-004 — worktree nao pode ser confundido com o checkout primario
    # ------------------------------------------------------------------
    def test_worktree_nao_confundido_com_checkout_primario(self):
        "@spec:AC-004 worktree nao e confundido com o checkout primario"
        cwd_checkout_primario = r"C:\Users\usuario\dev\app-exemplo"
        file_path_worktree = (
            r"C:\Users\usuario\dev\app-exemplo"
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
        cwd_checkout_primario = r"C:\Users\usuario\dev\app-exemplo"
        file_path_worktree = (
            r"C:\Users\usuario\dev\app-exemplo"
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
            r"C:\Users\usuario\dev\algum-projeto",
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
            r"C:\Users\usuario\dev\dashboard-im",
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
        cwd = r"C:\Users\usuario\dev\app-exemplo"
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
        repo_real = r"C:\dev\app-exemplo"
        r = classify(
            "Bash",
            {"command": f"cd {repo_real} && npx prisma migrate dev"},
            cwd_original,
        )
        db = [x for x in r if x.kind == "db"]
        self.assertEqual(len(db), 1)
        self.assertIn("app-exemplo", db[0].id)

    def test_migracao_de_schema_prisma_e_alembic(self):
        "@spec:AC-007 migracao de schema (prisma migrate / alembic) e reconhecida"
        cwd = r"C:\Users\usuario\dev\app-exemplo"
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
            {"file_path": r"C:\Users\usuario\dev\App\Index.JS", "content": "a"},
            r"C:\dev\x",
        )
        r2 = classify(
            "Write",
            {"file_path": "c:/users/usuario/dev/app/index.js", "content": "b"},
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
        # Contrapeso do conserto de posicao (21/09): o verbo dentro do literal
        # de um INTERPRETADOR continua sendo comando -- neutralizar esse literal
        # seria falso negativo, o erro caro.
        'powershell -c "Stop-Process -Name chrome"',
        'cmd /c "taskkill /F /IM chrome.exe"',
        "python -c \"import subprocess; subprocess.run(['taskkill','/F','/PID','1'])\"",
        # Os 6 bypasses que a auditoria adversarial de 21/09 abriu na PRIMEIRA
        # versao do conserto de posicao. A causa era uma so: eu tinha uma
        # allowlist de EXECUCAO (so `powershell -c`/`cmd /c` preservavam o
        # literal), entao toda forma de executar string que nao estivesse na
        # lista apagava o verbo junto com as aspas. Trocado por allowlist de
        # LEITURA (falha para o lado de detectar). Cada linha abaixo saiu de
        # uma medicao main-vs-patch, nao de hipotese.
        'iex "taskkill /F /IM chrome.exe"',
        'Invoke-Expression "Stop-Process -Name chrome -Force"',
        '& "taskkill" /F /IM chrome.exe',
        '$cmd = "taskkill"; & $cmd /F /IM chrome.exe',
        'powershell -Command "cmd /c taskkill /F /IM chrome.exe"',
        "wsl kill -9 1234",
        'Start-Process taskkill -ArgumentList "/F","/IM","chrome.exe"',
        # Segunda rodada, achados proprios ao medir a allowlist de LEITURA: o
        # risco dela e o inverso do anterior -- um leitor da lista usado para
        # EXECUTAR. `-exec` do find inicia comando novo; `xargs` so valia colado
        # no verbo; e `__import__('os').system(...)` nunca escreve o texto
        # `os.system`, entao escapava da guarda de execucao de dentro.
        "find . -exec taskkill /F /IM chrome.exe \\;",
        "grep -rl x . | xargs -I{} taskkill /F /PID 4242",
        "python -c \"__import__('os').system('taskkill /F /IM chrome.exe')\"",
        # Terceira rodada, 2a auditoria adversarial: LEITOR usado para EXECUTAR.
        # Ser `sed`/`awk`/`git` nao basta, e preservar o literal tambem nao
        # resolvia (o verbo fica atras de `1e `, `!` ou `system(`, que nao sao
        # inicio de segmento). O discriminante virou o ALVO dentro do literal.
        "sed '1e taskkill /F /IM chrome.exe' arquivo.txt",
        "awk 'BEGIN{system(\"taskkill /F /IM chrome.exe\")}'",
        "git -c alias.k='!taskkill /F /IM chrome.exe' k",
        "find . -ok taskkill /F /IM chrome.exe \\;",
        "find . -okdir taskkill /F /IM chrome.exe \\;",
        # Achados do DIFERENCIAL contra 29 mil comandos reais desta maquina --
        # os dois idiomas que ela de fato usa e que nenhuma auditoria pegou.
        "Get-CimInstance Win32_Process | ForEach-Object { Stop-Process -Name chrome -Force }",
        "/c/Windows/System32/taskkill.exe //PID 17556 //F",
        "C:\\Windows\\System32\\taskkill.exe /F /IM chrome.exe",
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

    # Achado 21/09/2026: a lista INOCENTES so cobre a palavra COLADA noutra
    # (`skills`, `killer_app`, `kill_switch`) -- essas o `\b` da lista explicita
    # ja protegia. A palavra ISOLADA, como argumento de busca ou dentro de um
    # literal, casava o gatilho e caia no fail-closed
    # `process:alvo-nao-identificado`, isto e, DENY DURO num comando que so LE.
    # Medido duas vezes na mesma sessao: um `python -c` lendo o events.log e um
    # `git worktree add -b fix/kill-trigger-posicao` (o gate barrou o conserto
    # do proprio gate, pelo nome do branch).
    MENCOES = [
        "grep -n 'kill' src/ccoord/policy.py",
        "python -c \"print('eventos de kill no log')\"",
        'echo "contando deny de kill"',
        "git worktree add ../x -b fix/kill-trigger-posicao",
        "rg --files-with-matches kill .",
        "python -c \"print('Stop-Process')\"",
        # Contrapeso da regra "literal com ALVO e comando": mencao sem alvo
        # continua sendo mencao, mesmo em leitor que executa string.
        'git commit -m "adiciona kill switch"',
        "sed -n '/kill/p' file.txt",
        "jq '.kill' data.json",
        'git log --grep="kill switch"',
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

    def test_alvo_aninhado_em_literal_sai_sem_pontuacao_grudada(self):
        "@spec:AC-003 alvo extraido de comando aninhado nao carrega aspa/parentese no id"
        # Detectar sem identificar nao protege: `claims.owner_of()` compara
        # string EXATA, entao `browser:chrome.exe')` nao casa com o claim
        # `browser:chrome.exe` e o kill sairia LIBERADO -- mesmo modo de falha
        # do curinga, por outra porta.
        recursos = classify(
            "Bash",
            {"command": "python -c \"__import__('os').system('taskkill /F /IM chrome.exe')\""},
            "C:/tmp",
        )
        ids = {r.id for r in recursos}
        self.assertIn("browser:chrome.exe", ids, f"id sujo nao casa com claim: {ids}")

    def test_alvo_normalizado_casa_com_a_chave_do_claim(self):
        "@spec:AC-003 redirecionamento, comando colado e caminho com espaco nao sujam o id do alvo"
        # Tres defeitos PRE-EXISTENTES achados pela 2a auditoria adversarial em
        # 21/09 -- nao vieram do conserto de posicao, ja estavam em producao.
        # Nos tres o gate DETECTAVA e liberava assim mesmo, porque
        # `claims.owner_of()` compara string exata e o id saia sujo.
        casos = [
            # `>nul` do cmd.exe grudava no nome do executavel
            ("taskkill /F /IM chrome.exe>nul", "browser:chrome.exe"),
            # dois kills no mesmo comando, sem espaco antes do `;`
            ("Stop-Process -Name chrome.exe;Stop-Process -Name notepad.exe", "browser:chrome.exe"),
            # caminho completo entre aspas: o alvo saia como `c:\\program`
            ('taskkill /F /IM "C:\\Program Files\\Google\\Chrome\\chrome.exe"', "browser:chrome.exe"),
            # 3a auditoria: escape que o PROPRIO shell come antes de executar.
            # Provado em runtime pelo auditor -- `cmd //c "echo ch^rome.exe"`
            # imprime `chrome.exe`, e o backtick some no parser do PowerShell
            # mesmo fora de string. O taskkill real mata o chrome; o
            # classificador via um nome que nao existe.
            ("taskkill /F /IM ch^rome.exe", "browser:chrome.exe"),
            ("taskkill /F /IM chro`me.exe", "browser:chrome.exe"),
            # 3a auditoria: DECOY. O extrator pegava o primeiro `/im` do texto
            # inteiro, entao um `echo` inocente antes sequestrava o alvo e o
            # gate passava a proteger o processo errado -- pior que nao
            # proteger, porque parece protegido.
            ('echo teste /im "decoy.exe" ; taskkill /F /IM chrome.exe', "browser:chrome.exe"),
            ('echo -name "decoy-proc" ; Stop-Process -Name chrome.exe', "browser:chrome.exe"),
            # Controle: alvo legitimo ANTES do verbo (idioma real do CIM) tem
            # de continuar sendo achado -- e o que impede o conserto do decoy
            # de virar falso negativo.
            (
                'Get-CimInstance Win32_Process -Filter "Name=\'chrome.exe\'" | ForEach-Object { Stop-Process -Id $_.ProcessId }',
                "browser:chrome.exe",
            ),
        ]
        for comando, esperado in casos:
            with self.subTest(comando=comando):
                ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
                self.assertIn(
                    esperado,
                    ids,
                    f"id sujo nao casa com claim -> kill sai LIBERADO: {ids}",
                )

    def test_decoy_em_outro_comando_nao_sequestra_o_alvo(self):
        "@spec:AC-003 alvo plantado em comando anterior nao vira o alvo do kill"
        # 4a auditoria: o fallback "procura no comando inteiro" reabria o decoy
        # justamente no idioma `Get-Process X | Stop-Process`, onde o trecho
        # depois do verbo nao tem flag de alvo nenhuma. O pior caso nao e
        # deixar passar: e ACUSAR O PROCESSO ERRADO -- `browser:firefox.exe`
        # enquanto o chrome de uma peer morre, com o gate parecendo conferido.
        casos = [
            ('echo "/im decoy.exe" ; Get-Process chrome | Stop-Process', "decoy.exe"),
            ('echo "-id 9999" ; Get-Process chrome | Stop-Process', "9999"),
            ('echo "/im firefox.exe" ; Get-Process chrome | Stop-Process', "firefox.exe"),
            ('echo "/im chrome.exe" ; Get-Process notepad | Stop-Process', "chrome.exe"),
        ]
        for comando, decoy in casos:
            with self.subTest(comando=comando):
                ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
                self.assertFalse(
                    any(decoy in i for i in ids),
                    f"decoy {decoy!r} sequestrou o alvo -> gate protege o processo ERRADO: {ids}",
                )
                self.assertTrue(ids, f"o kill tem de continuar sendo detectado: {ids}")

    def test_decoy_DEPOIS_do_verbo_tambem_nao_sequestra_o_alvo(self):
        "@spec:AC-003 decoy plantado em comando posterior tambem nao vira o alvo do kill"
        # A 5a auditoria achou o simetrico do caso anterior, e ele e pior:
        # o caminho PRIMARIO da extracao (`command[pos_do_verbo:]`) ia ate o FIM
        # da string, atravessando `;`/`&`/`\n`. So o fallback estava preso ao
        # segmento -- entao um decoy DEPOIS do verbo sequestrava o alvo antes de
        # o trecho protegido sequer ser tentado. Pre-existente (identico no
        # codigo antigo), mas o comentario e a mensagem do commit alegavam
        # protecao que nao existia nesse caminho.
        casos = [
            ('taskkill /F /PID 1234 & echo "/im chrome.exe"', "1234", "chrome.exe"),
            ("taskkill /F /PID 1234 ; echo /im chrome.exe", "1234", "chrome.exe"),
            ('Stop-Process -Id 1234 ; echo "-Name chrome.exe"', "1234", "chrome.exe"),
            ('pkill -9 sshd ; echo "-Name chrome.exe"', "sshd", "chrome.exe"),
        ]
        for comando, real, decoy in casos:
            with self.subTest(comando=comando):
                ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
                self.assertFalse(
                    any(decoy in i for i in ids),
                    f"decoy posterior sequestrou o alvo -> gate protege o errado: {ids}",
                )
                self.assertTrue(
                    any(real in i for i in ids),
                    f"o alvo REAL tem de ser identificado, senao so troquei um erro por outro: {ids}",
                )

    def test_verbo_decorativo_em_outro_segmento_nao_vira_a_ancora(self):
        "@spec:AC-003 a palavra dentro de um echo nao ancora o segmento do kill real"
        # 6a auditoria, e este foi INTRODUZIDO por mim: ao prender a extracao ao
        # segmento do verbo, a ancora passou a ser a PRIMEIRA ocorrencia da
        # palavra em qualquer lugar -- inclusive dentro de `echo "kill ..."`.
        # O segmento passou a ser o do echo, e o comando real ficou invisivel.
        # Os dois primeiros casos sao os graves: devolvem alvo ERRADO, entao o
        # gate checa o claim de um processo que ninguem vai matar e LIBERA.
        casos = [
            ('echo "kill 99 please"; taskkill /F /IM chrome.exe', "chrome.exe", "99"),
            ('echo "note kill 4321 later"; Stop-Process -Id 1234 -Force', "1234", "4321"),
            ('echo "---kill stray edge---"; taskkill //IM msedge.exe //F', "msedge.exe", None),
        ]
        for comando, real, errado in casos:
            with self.subTest(comando=comando):
                ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
                self.assertTrue(
                    any(real in i for i in ids),
                    f"o alvo REAL sumiu; a ancora caiu no verbo decorativo: {ids}",
                )
                # O alvo do texto decorativo PODE aparecer junto: hoje todos os
                # verbos em posicao de comando viram alvo, e `kill 99` dentro de
                # um echo e indistinguivel de comando sem executar o shell. O
                # que nao pode e o alvo REAL sumir -- um id a mais custa uma
                # checagem de claim, um id a menos custa o processo da peer.
                del errado

    def test_alvo_dentro_de_segmento_de_leitor_nunca_conta(self):
        "@spec:AC-003 texto em segmento comandado por leitor nao vira alvo, nem a esquerda do verbo"
        # 7a auditoria: a busca a esquerda do verbo e o ramo nao-verbal liam o
        # texto CRU, entao um `echo` anterior sequestrava a identificacao. Nos
        # dois primeiros casos o resultado e ACUSAR O PROCESSO ERRADO, que
        # converte o fail-closed em allow silencioso.
        casos = [
            # decoy no echo; o verbo nao tem alvo proprio -> tem de dar fail-closed
            ('echo "/IM chrome.exe"; Stop-Process -Id $badVar -Force',
             "alvo-nao-identificado", "chrome.exe"),
            # decoy no echo, MAIS PROXIMO do verbo que a selecao real do CIM
            ("Get-CimInstance -Filter \"Name='chrome.exe'\" | Select -First 1 | %{ $id=$_.ProcessId }; "
             "echo \"Name='notepad.exe'\"; Stop-Process -Id $id -Force",
             "chrome.exe", "notepad.exe"),
            # ramo NAO VERBAL (Invoke-CimMethod): nao ha verbo para ancorar
            ("echo \"Name='decoy.exe'\" ; Invoke-CimMethod -Query "
             "\"select * from Win32_Process where Name='chrome.exe'\" -MethodName Terminate",
             "chrome.exe", "decoy.exe"),
        ]
        for comando, esperado, decoy in casos:
            with self.subTest(comando=comando[:60]):
                ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
                self.assertFalse(
                    any(decoy in i for i in ids),
                    f"decoy de segmento de leitor venceu -> gate acusa o processo errado: {ids}",
                )
                self.assertTrue(
                    any(esperado in i for i in ids),
                    f"esperado {esperado!r} no resultado: {ids}",
                )

    def test_excesso_de_verbos_cai_em_fail_closed_e_nao_em_silencio(self):
        "@spec:AC-009 comando com mais kills que o teto de ancoras recusa, nao omite"
        # 7a auditoria: o teto de 8 ancoras (existe para o p95 do caminho quente)
        # simplesmente DESCARTAVA o 9o e o 10o kill -- eles nao viravam recurso
        # nenhum, entao nao passavam por `decide()` e saiam allow por omissao.
        # Teto e limite de custo, nunca licenca para ignorar em silencio.
        comando = " & ".join(f"taskkill /F /IM proc{i}.exe" for i in range(1, 11))
        ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
        self.assertIn(
            "process:alvo-nao-identificado",
            ids,
            f"excedeu o teto e nao emitiu o sentinela de fail-closed: {sorted(ids)}",
        )

    def test_verbo_sem_alvo_emite_sentinela_mesmo_havendo_outro_kill(self):
        "@spec:AC-009 kill cujo alvo nao pode ser identificado nao some quando ha outro kill no comando"
        # 8a auditoria: isolado, `find . -exec kill -9 4321 \;` ja caia em
        # fail-closed. Mas com um SEGUNDO kill no mesmo comando, o 4321 sumia da
        # lista inteira -- nao virava recurso nem sentinela, entao `decide()`
        # nunca era chamada para ele, e o allow do outro alvo liberava os DOIS.
        # A causa e uma assimetria: `_SEP_ENTRE_COMANDOS` nao corta em `|` nem
        # em `{`, mas `_KILL_VERBO_EM_POSICAO` trata os dois como inicio de
        # comando -- entao o verbo real cai no segmento do token leitor.
        casos = [
            'find . -iname "*.pid" -exec kill -9 4321 \\; ; taskkill /F /IM chrome.exe',
            "git log | ForEach-Object { Stop-Process -Id 4321 -Force }; taskkill /F /IM notepad.exe",
        ]
        for comando in casos:
            with self.subTest(comando=comando[:50]):
                ids = {r.id for r in classify("Bash", {"command": comando}, "C:/tmp")}
                self.assertTrue(
                    any("4321" in i for i in ids) or "process:alvo-nao-identificado" in ids,
                    f"o kill do 4321 sumiu sem sentinela -> allow por omissao: {sorted(ids)}",
                )

    def test_alvo_em_segmento_anterior_ao_verbo_continua_sendo_achado(self):
        "@spec:AC-003 `Get-Process X;Stop-Process` acha X, e o decoy mais distante perde"
        # Idioma real de limpeza de chrome orfao do Playwright MCP: o nome vem
        # num comando anterior, separado por `;`. Prender ao segmento tinha
        # quebrado isso (virava deny). A regra que concilia com o decoy: vale a
        # ocorrencia MAIS PROXIMA a esquerda do verbo.
        ids = {r.id for r in classify(
            "Bash", {"command": "Get-Process chrome;Stop-Process"}, "C:/tmp"
        )}
        self.assertTrue(any("chrome" in i for i in ids), f"alvo legitimo perdido: {ids}")

    def test_get_process_no_pipe_tambem_respeita_o_segmento(self):
        "@spec:AC-003 o nome vindo de `Get-Process X | Stop-Process` sai do segmento certo"
        # Mesma familia, outro extrator: `_GET_PROCESS_NOME` tambem varria o
        # comando inteiro. Pre-existente, apontado como nota lateral na 5a
        # auditoria e corrigido junto por ser a mesma classe.
        ids = {r.id for r in classify(
            "Bash",
            {"command": "Get-Process decoyprocess ; Get-Process chrome | Stop-Process"},
            "C:/tmp",
        )}
        self.assertFalse(
            any("decoyprocess" in i for i in ids), f"decoy de outro segmento venceu: {ids}"
        )
        self.assertTrue(any("chrome" in i for i in ids), f"alvo real perdido: {ids}")

    def test_alvo_do_pipe_do_get_process_continua_sendo_achado(self):
        "@spec:AC-003 contrapeso: `Get-Process X | Stop-Process` sem decoy identifica X"
        ids = {r.id for r in classify(
            "Bash", {"command": "Get-Process chrome | Stop-Process -Force"}, "C:/tmp"
        )}
        self.assertTrue(
            any("chrome" in i for i in ids),
            f"sem decoy o alvo do pipe tem de ser identificado, senao o conserto virou cegueira: {ids}",
        )

    def test_mencao_da_palavra_fora_de_posicao_de_comando_nao_vira_kill(self):
        "@spec:AC-003 palavra isolada em argumento ou dentro de literal nao vira recurso de processo"
        for comando in self.MENCOES:
            with self.subTest(comando=comando):
                recursos = classify("Bash", {"command": comando}, "C:/tmp")
                tipos = [r.kind for r in recursos]
                ids = [r.id for r in recursos]
                self.assertFalse(
                    any(t in ("browser", "process") for t in tipos),
                    f"falso positivo (vira DENY fail-closed): {comando!r} -> {ids}",
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


class TestNomeCurto8_3(unittest.TestCase):
    """Gate CEGO medido no ensaio com duas sessoes reais (12/09).

    Uma sessao segurava `...\\VINICI~1\\...\\compartilhado.py` e a outra editava
    `...\\usuario\\...\\compartilhado.py`. `os.path.samefile` = True,
    ids diferentes, nenhuma via a outra. O diretorio de scratchpad entregue a
    cada sessao vem no formato 8.3, entao isto valeria para quase toda sessao.
    """

    def test_curto_e_longo_do_mesmo_arquivo_colapsam_no_mesmo_id(self):
        "@spec:AC-004 caminho 8.3 e caminho longo do MESMO arquivo tem o mesmo id"
        import os
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ccoord ensaio ") as base:
            # nome com espaco força o Windows a gerar o alias 8.3
            longo = os.path.join(base, "arquivo alvo.txt")
            with open(longo, "w", encoding="utf-8") as f:
                f.write("x")
            buf = ctypes.create_unicode_buffer(32768)
            n = ctypes.windll.kernel32.GetShortPathNameW(longo, buf, len(buf))
            curto = buf.value if 0 < n < len(buf) else ""
            if not curto or "~" not in curto:
                # volume sem 8.3: o repro do ensaio nao pode ser montado aqui.
                # Fica a prova direta do resolvedor, que nao depende do volume.
                self.assertEqual(_resolver_nome_curto(longo), longo)
                return
            id_curto = classify("Write", {"file_path": curto, "content": "a"}, "")[0].id
            id_longo = classify("Write", {"file_path": longo, "content": "a"}, "")[0].id
            self.assertTrue(os.path.samefile(curto, longo))
            self.assertEqual(id_curto, id_longo, "mesmo arquivo tem de ter o mesmo id")

    def test_caminho_sem_8_3_nao_consulta_o_so(self):
        "@spec:AC-004 caminho sem `~N` e identidade pura (nao paga consulta ao SO)"
        for p in (r"C:\dev\repo\app.js", r"\\server\share\x.md", "relativo.txt", ""):
            with self.subTest(p=p):
                self.assertEqual(_resolver_nome_curto(p), p)

    def test_caminho_8_3_inexistente_devolve_o_original(self):
        "@spec:AC-004 8.3 que nao existe no disco devolve o original, nao inventa"
        fantasma = r"Q:\NAOEXI~1\tambem\nao\existe.txt"
        self.assertEqual(_resolver_nome_curto(fantasma), fantasma)


class TestCwdEfetivoNoEnsaioReal(unittest.TestCase):
    """Falso negativo pego pelo ENSAIO com sessao real (12/09), nao pelos testes.

    O `_efetivo_cwd` so reconhecia `cd <dir> &&` colado no INICIO do comando.
    Um bloco de shell de varias linhas -- a forma mais comum de comando que eu
    mesma escrevo -- deixava o `cd` invisivel, e todo caminho relativo era
    resolvido contra o cwd da SESSAO. Medido no ensaio: 3 claims criados sobre
    `C:\\...\\dev\\cc-coord\\alvo.txt`, arquivo que nao existe, enquanto o
    arquivo real (em outro diretorio) ficava sem claim nenhum. Isso e pior que
    ausencia de gate: protege fantasma e deixa o alvo real aberto.

    Segundo defeito na mesma funcao: `cd "$VAR"` era tratado como se `$VAR`
    fosse o NOME de um diretorio, entao o cwd virava `<cwd>\\$VAR` -- um
    caminho inventado. Nao da para saber o valor da variavel sem executar o
    comando, entao o unico resultado honesto e "cwd desconhecido", e com cwd
    desconhecido um caminho RELATIVO nao pode virar claim.
    """

    def _arquivos(self, comando, cwd=r"C:\dev\sessao"):
        return [r for r in classify("Bash", {"command": comando}, cwd) if r.kind == "file"]

    def test_cd_em_linha_separada_muda_o_cwd_efetivo(self):
        "@spec:AC-BashWrite cd em linha propria (bloco multilinha) e respeitado, nao so `cd X &&`"
        multilinha = "cd C:\\dev\\real\necho x > alvo.txt"
        encadeado = classify(
            "Bash", {"command": "cd C:\\dev\\real && echo x > alvo.txt"}, r"C:\dev\sessao"
        )
        alvo_multi = self._arquivos(multilinha)
        alvo_enc = [r for r in encadeado if r.kind == "file"]
        self.assertEqual(len(alvo_multi), 1)
        self.assertEqual(alvo_multi[0].id, alvo_enc[0].id)
        self.assertNotIn("sessao", alvo_multi[0].id)

    def test_cd_depois_de_outro_comando_na_cadeia(self):
        "@spec:AC-BashWrite cd no MEIO da cadeia conta (nao so no inicio do comando)"
        alvo = self._arquivos("mkdir -p C:\\dev\\real && cd C:\\dev\\real && echo x > alvo.txt")
        self.assertEqual(len(alvo), 1)
        self.assertIn("c--dev-real-alvo.txt", alvo[0].id)

    def test_cd_para_variavel_nao_inventa_caminho(self):
        "@spec:AC-BashWrite cd para valor nao-literal ($VAR) torna o cwd desconhecido: relativo nao vira claim"
        alvo = self._arquivos('TD="C:/x"\ncd "$TD"\necho x > alvo.txt')
        self.assertEqual(
            alvo,
            [],
            "com cwd desconhecido, resolver o relativo contra o cwd da sessao cria claim fantasma",
        )

    def test_cwd_desconhecido_nao_apaga_caminho_absoluto(self):
        "@spec:AC-BashWrite cwd desconhecido nao cega o gate: alvo ABSOLUTO continua virando claim"
        alvo = self._arquivos('cd "$TD"\necho x > C:\\dev\\real\\alvo.txt')
        self.assertEqual(len(alvo), 1)
        self.assertIn("c--dev-real-alvo.txt", alvo[0].id)

    def test_maior_que_dentro_de_aspas_nao_e_redirecionamento(self):
        "@spec:AC-BashWrite `>` dentro de string e dado, nao redirecionamento"
        # Medido no ensaio: `python -c "print(a, '->', b)"` criava claim sobre um
        # arquivo chamado `, e[`. Claim sobre arquivo inventado e ruido, e ruido
        # ensina a ignorar o aviso.
        for inocente in (
            "python -c \"print(e['event'], '->', e['path'])\"",
            'echo "a > b.txt"',
            "grep 'x>y' arquivo.txt",
        ):
            with self.subTest(comando=inocente):
                self.assertEqual(self._arquivos(inocente), [])

    def test_redirecionamento_de_verdade_continua_valendo(self):
        "@spec:AC-BashWrite mascarar aspas nao pode apagar redirecionamento real (nao-vacuidade)"
        simples = self._arquivos("echo x > real.txt")
        self.assertEqual(len(simples), 1)
        self.assertIn("real.txt", simples[0].id)
        # alvo ENTRE aspas: o `>` esta fora delas, entao continua contando
        com_espaco = self._arquivos('echo x > "meu arquivo.txt"')
        self.assertEqual(len(com_espaco), 1)
        self.assertIn("meu arquivo.txt", com_espaco[0].path.lower())
        # texto entre aspas ANTES de um redirecionamento real nao pode desarma-lo
        misto = self._arquivos('echo "a -> b" > real.txt')
        self.assertEqual(len(misto), 1)
        self.assertIn("real.txt", misto[0].id)

    def test_til_no_meio_do_caminho_e_nome_curto_8_3_nao_valor_de_runtime(self):
        "@spec:AC-BashWrite ~ no MEIO do caminho e nome curto 8.3 do Windows, nao HOME"
        # Regressao que eu mesma introduzi e o ensaio pegou: marcar `~` como
        # nao-literal cegava o gate em `C:\\Users\\VINICI~1\\AppData\\...` -- o
        # caminho do scratchpad desta maquina, o diretorio mais usado da sessao.
        curto = r"C:\Users\VINICI~1\AppData\Local\Temp\x"
        alvo = self._arquivos(f"cd {curto}\necho a > relativo.txt")
        self.assertEqual(len(alvo), 1, "`~` no meio do caminho nao pode zerar o gate")
        self.assertIn("temp-x-relativo.txt", alvo[0].id)

    def test_til_no_inicio_continua_sendo_home_desconhecido(self):
        "@spec:AC-BashWrite ~ como PRIMEIRO caractere segue sendo HOME (destino de runtime)"
        self.assertEqual(self._arquivos("cd ~/dev\necho a > relativo.txt"), [])

    def test_sem_cd_o_relativo_continua_resolvendo_contra_a_sessao(self):
        "@spec:AC-BashWrite sem cd nenhum, o comportamento antigo permanece (nao-vacuidade)"
        alvo = self._arquivos("echo x > alvo.txt")
        self.assertEqual(len(alvo), 1)
        self.assertIn("c--dev-sessao-alvo.txt", alvo[0].id)

    def test_cwd_desconhecido_nao_acusa_repo_no_commit(self):
        "@spec:AC-007 com cwd desconhecido, nao afirmar em QUAL repo o commit acontece"
        recursos = classify(
            "Bash", {"command": 'cd "$REPO"\ngit commit -m x'}, r"C:\dev\sessao"
        )
        repos = [r for r in recursos if r.kind == "git"]
        self.assertEqual(
            repos,
            [],
            "apontar o repo da sessao aqui recusaria o commit citando o repo errado",
        )
        # Contraprova: sem o `cd $VAR`, o MESMO commit tem de ser reconhecido --
        # senao este teste passaria por classify() ter parado de ver git.
        normal = classify("Bash", {"command": "git commit -m x"}, r"C:\dev\sessao")
        self.assertTrue(any(r.kind == "git" for r in normal))
        # E um `git -C <repo>` explicito sobrevive ao cwd desconhecido.
        explicito = classify(
            "Bash",
            {"command": 'cd "$REPO"\ngit -C C:\\dev\\real commit -m x'},
            r"C:\dev\sessao",
        )
        self.assertTrue(any(r.kind == "git" for r in explicito))
