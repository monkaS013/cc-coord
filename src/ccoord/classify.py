"""ccoord.classify — mapeia uma chamada de ferramenta para os recursos que ela toca.

Funcao PURA: (tool_name, tool_input, cwd, ler_arquivo=None) -> list[Resource].
Nenhum I/O acontece aqui dentro — nem abrir arquivo, nem chamar git, nem
consultar processo. A unica leitura de arquivo (para derivar a faixa de linha
de um Edit) entra por injecao via o parametro `ler_arquivo`.

Contexto (ler antes de mexer):
- design.md secao 3.3 — as regras que nao podem ser erradas.
- spec.md secao 5.1 — o namespace de recursos (`file:`, `git:`, `port:`,
  `server:`, `db:`, `browser:`).
- medicao-hooks.md secao 2 — o formato real do `tool_input` de `PreToolUse`
  (`Bash` so tem `command`; `Edit`/`Write` tem `file_path`; `NotebookEdit`
  tem `notebook_path`, nao `file_path` — confirmado no schema real da
  ferramenta, nao no design.md, que generaliza como se fosse sempre
  `file_path`).
"""

from __future__ import annotations

import functools
import itertools
import re

from ccoord.paths import resolver_nome_curto as _resolver_nome_curto

# `LerArquivo` (assinatura da funcao injetada de leitura de arquivo, usada so
# em anotacoes) NAO e um alias `Callable[[str], Optional[str]]` de verdade:
# isso exigiria `import typing` em tempo de import (~2ms a toa no caminho
# quente - RNF-04/T-017), so para uma anotacao que `from __future__ import
# annotations` ja deixa como string, nunca avaliada. Contrato documentado
# aqui: `ler_arquivo(path: str) -> str | None`.
#
# `Resource` e classe simples (nao `@dataclass`, mesmo motivo de
# `ccoord.claims`/`ccoord.sessions`: o decorator importa `inspect`, ~10ms
# fixos por hook antes desta mudanca).


class Resource:
    """Um recurso tocado por uma chamada de ferramenta.

    - kind: "file" | "browser" | "port" | "server" | "git" | "db" | "process"
    - id: identificador no namespace da spec §5.1 (ex. "file:c--dev-...-app.js",
      "port:3100", "git:c--dev-app-exemplo"). Sempre em minusculas — ver
      `_normalize_path` para a razao (case-insensitividade do Windows).
    - path: caminho normalizado do arquivo/repo, no formato de exibicao
      (drive maiusculo, barras invertidas), quando aplicavel; None quando o
      recurso nao e um caminho (port, browser, process).
    - lines: (inicio, fim) 1-indexado e inclusivo, ou None quando a claim e
      do arquivo inteiro (degradacao explicita, nunca erro).
    - action: "edit" | "write" | "kill" | "commit" | "push" | "reset" |
      "checkout" | "bind" | "migrate".
    """

    def __init__(
        self,
        kind: str,
        id: str,
        path: str | None = None,
        lines: tuple[int, int] | None = None,
        action: str = "",
    ) -> None:
        self.kind = kind
        self.id = id
        self.path = path
        self.lines = lines
        self.action = action


# ---------------------------------------------------------------------------
# Normalizacao de caminho (regra 5): mesmo arquivo, duas grafias, mesmo id.
# ---------------------------------------------------------------------------

_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


def _normalize_path(path: str, cwd: str = "") -> Tuple[str, str]:
    """Normaliza um caminho Windows/POSIX de forma deterministica.

    Devolve (path_exibicao, chave):
    - path_exibicao: caminho absoluto com barras invertidas, drive
      maiusculo, segmentos "." e ".." resolvidos, preservando o case
      original dos demais segmentos (para exibicao/log).
    - chave: a mesma resolucao de caminho, com barras normais e tudo em
      minusculas — e essa chave que vira o "id" do recurso, porque o
      Windows e case-insensitive (e barra normal / invertida sao a mesma
      coisa): "C:\\Dev\\App.JS" e "c:/dev/app.js" tem que colapsar no
      mesmo recurso, ou um claim nao protege o outro.

    Sem I/O: e so manipulacao de string. Nao usa os.path.abspath/normpath
    (que dependeriam de cwd real ou do SO) para manter o resultado
    deterministico em qualquer maquina que rode o teste — `cwd`, quando
    passado, e so mais uma STRING de entrada (nunca uma consulta ao SO).

    Achado #1 (3a auditoria de classify.py, 11/09-12/09) — dois defeitos que
    o codigo original tinha ao mesmo tempo, corrigidos juntos porque a causa
    e a mesma (o numero de barras iniciais era descartado sem contagem):
      1. 0, 1, 2 ou 3+ barras iniciais (relativo / raiz-relativo-da-unidade /
         UNC de rede / variante malformada de UNC) todas colapsavam no MESMO
         resultado — um `\\server\\share\\x` (UNC de rede) virava
         indistinguivel de `\\server\\share\\x` (raiz-relativa de unidade,
         1 barra). Agora: 0 barras = relativo (junta com `cwd` inteiro
         abaixo); 1 barra = raiz-relativa (junta so o DRIVE do `cwd` — e a
         semantica real do Windows: "\\Users\\x" com cwd em D:\\... quer
         dizer D:\\Users\\x); 2+ barras = UNC (nunca vira raiz-relativa).
      2. Um `file_path` relativo (sem drive, sem barra inicial — nao deveria
         ocorrer via schema real de Edit/Write, mas o codigo nao rejeita)
         descartava o `cwd` por completo: dois arquivos REAIS diferentes
         (`notas\\x.md` em dois projetos com cwd diferente) colapsavam no
         mesmo id. Agora junta com o `cwd` recebido (ver bloco de join
         abaixo) antes de resolver ".."/"." — sem isso, chavear em
         `file_path` (regra da secao 3.3, nunca em `cwd`) so funciona
         quando `file_path` ja e absoluto.

    Achado #2 (mesma auditoria) tem DUAS partes; so uma e corrigida aqui:
      - Prefixo de caminho longo do Windows (`\\\\?\\`, e a variante
        `\\\\?\\UNC\\...`) e SO um marcador de sintaxe para o SO ignorar
        MAX_PATH — nao e um namespace diferente. Ferramentas adicionam esse
        prefixo sozinhas para paths >260 chars; sem remove-lo, o MESMO
        arquivo virava dois ids diferentes so pela presenca do prefixo.
        Manipulacao pura de string (remover 4-8 chars fixos), sem IO —
        corrigido abaixo.
      - Unidade mapeada (`Z:\\...`) apontando para o MESMO recurso de rede
        que um caminho UNC (`\\\\server\\share\\...`) e um caso DIFERENTE:
        resolver isso exigiria consultar o SO (qual UNC uma letra de unidade
        resolve agora), o que este modulo explicitamente evita por
        determinismo/custo (RNF-04, docstring acima). Continua NAO resolvido
        — mas agora e uma decisao CONSCIENTE e documentada (o achado #2
        apontava que antes ninguem tinha registrado isso em ASM-xxx/Q-xxx
        nenhum): duas sessoes, uma via unidade mapeada e outra via UNC, no
        MESMO arquivo fisico, continuam com claims em ids diferentes e sem
        aviso uma da outra. Ver teste
        `test_unidade_mapeada_vs_unc_e_limitacao_conhecida_sem_io` em
        tests/test_classify.py.
    """
    bruto = _resolver_nome_curto((path or "").strip()).replace("\\", "/")

    # \\?\ (prefixo de caminho estendido do Windows) e \\?\UNC\... — string
    # pura, sem IO (achado #2, parte corrigivel). \\?\C:\x -> C:\x;
    # \\?\UNC\server\share -> \\server\share (2 barras, mesmo tratamento de
    # UNC abaixo).
    if bruto[:4] == "//?/":
        resto = bruto[4:]
        if resto[:4].lower() == "unc/":
            bruto = "//" + resto[4:]
        else:
            bruto = resto

    n_barras = len(bruto) - len(bruto.lstrip("/"))
    sem_barras_provisorio = bruto[n_barras:]
    primeiro_seg = sem_barras_provisorio.split("/", 1)[0] if sem_barras_provisorio else ""
    tem_drive_proprio = n_barras < 2 and bool(_DRIVE_RE.match(primeiro_seg))

    # Junta com `cwd` SO quando o proprio caminho nao tem drive e nao e UNC
    # (achado #1, ponto 2 do docstring acima). 0 barras = relativo puro,
    # junta o cwd INTEIRO; 1 barra = raiz-relativa, junta so o DRIVE do cwd.
    if not tem_drive_proprio and n_barras < 2 and cwd:
        cwd_limpo = (cwd or "").strip().replace("\\", "/")
        if n_barras == 0 and cwd_limpo:
            bruto = cwd_limpo.rstrip("/") + "/" + bruto
        elif n_barras == 1 and cwd_limpo:
            cwd_n_barras = len(cwd_limpo) - len(cwd_limpo.lstrip("/"))
            cwd_primeiro = cwd_limpo[cwd_n_barras:].split("/", 1)[0]
            if _DRIVE_RE.match(cwd_primeiro):
                bruto = cwd_primeiro + bruto  # "/x" -> "C:/x" (so o drive)
        n_barras = len(bruto) - len(bruto.lstrip("/"))

    # 2+ barras (depois do join acima, que pode ate promover um caminho
    # relativo a UNC se o proprio `cwd` for UNC) = rede, nunca raiz-relativo
    # (achado #1, ponto 1). 3+ e variante malformada de UNC — tratada igual.
    eh_unc = n_barras >= 2
    segs = bruto[n_barras:].split("/")

    drive = None
    inicio = 0
    if not eh_unc and segs and _DRIVE_RE.match(segs[0]):
        drive = segs[0][0].upper() + ":"
        inicio = 1

    resolvidos: list = []
    for seg in segs[inicio:]:
        if seg in ("", "."):
            continue
        if seg == "..":
            if resolvidos:
                resolvidos.pop()
            continue
        resolvidos.append(seg)

    corpo = "/".join(resolvidos)
    if eh_unc:
        # Marca "//" (2 barras) — vira "--" no slug de `_path_to_id`,
        # distinto de raiz-relativa (1 barra -> "-") e de drive (1 letra +
        # "-"). Sem essa marca dupla, UNC colapsava com raiz-relativa
        # (achado #1).
        path_exibicao = ("//" + corpo) if corpo else "//"
        chave = "//" + corpo.lower()
    elif drive is not None:
        path_exibicao = (drive + "/" + corpo) if corpo else (drive + "/")
        chave = (drive + "/" + corpo).lower()
    else:
        path_exibicao = ("/" + corpo) if corpo else "/"
        chave = ("/" + corpo).lower()
    path_exibicao = path_exibicao.replace("/", "\\")

    return path_exibicao, chave


def _path_to_id(kind: str, path: str, cwd: str = "") -> Tuple[str, str]:
    """Monta o id `kind:slug` (spec §5.1) e devolve junto o path de exibicao."""
    path_exibicao, chave = _normalize_path(path, cwd)
    slug = chave.replace(":", "-").replace("/", "-")
    return f"{kind}:{slug}", path_exibicao


def _repo_short_name(path: str) -> str:
    """Nome curto do repo (ultimo segmento do caminho), para `db:`/`server:`.

    A spec §5.1 usa nome curto nesses dois ("db:workday:migrations",
    "server:dashboard-im:8099"), diferente de `git:` que usa o path
    absoluto inteiro ("git:C--dev-app-exemplo"). Ver decisao no relato
    final. Sempre chamada com `path` ja absoluto (o `cwd` efetivo, ver
    `_efetivo_cwd`) — sem cwd adicional para juntar.
    """
    _, chave = _normalize_path(path)
    segs = [s for s in chave.split("/") if s]
    return segs[-1] if segs else chave


def _resource_from_path(kind: str, path: str, action: str, lines=None, cwd: str = "") -> Resource:
    id_, path_exibicao = _path_to_id(kind, path, cwd)
    return Resource(kind=kind, id=id_, path=path_exibicao, lines=lines, action=action)


# ---------------------------------------------------------------------------
# Edit — faixa de linha derivada do old_string (regra 2).
# ---------------------------------------------------------------------------


def _faixa_de_linha(conteudo: str, old_string: str) -> Optional[Tuple[int, int]]:
    """Localiza `old_string` em `conteudo` e devolve (inicio, fim) 1-indexado.

    None quando `old_string` nao e encontrado — degradacao explicita para
    claim do arquivo inteiro, nunca erro.
    """
    if not old_string:
        return None
    idx = conteudo.find(old_string)
    if idx == -1:
        return None
    inicio = conteudo.count("\n", 0, idx) + 1
    fim = inicio + old_string.count("\n")
    return (inicio, fim)


def _classify_edit(tool_input: dict, cwd: str, ler_arquivo: Optional[LerArquivo]):
    file_path = tool_input.get("file_path")
    if not file_path:
        return []

    old_string = tool_input.get("old_string", "")
    lines = None
    if ler_arquivo is not None and old_string:
        try:
            conteudo = ler_arquivo(file_path)
        except Exception:
            conteudo = None
        if conteudo is not None:
            lines = _faixa_de_linha(conteudo, old_string)

    return [_resource_from_path("file", file_path, "edit", lines, cwd)]


def _classify_write(tool_input: dict, cwd: str):
    file_path = tool_input.get("file_path")
    if not file_path:
        return []
    # Write e sempre reescrita do arquivo inteiro (regra 3) — nunca faixa.
    return [_resource_from_path("file", file_path, "write", None, cwd)]


def _classify_notebook_edit(tool_input: dict, cwd: str):
    # NotebookEdit usa `notebook_path`, nao `file_path` (schema real da
    # ferramenta) — o design.md generaliza a regra 1 como se a chave fosse
    # sempre `file_path`; aqui tratamos a chave certa por ferramenta.
    notebook_path = tool_input.get("notebook_path")
    if not notebook_path:
        return []
    edit_mode = tool_input.get("edit_mode", "replace")
    # Edicao por celula, nao por faixa de linha do arquivo: sempre lines=None.
    action = "write" if edit_mode == "insert" else "edit"
    return [_resource_from_path("file", notebook_path, action, None, cwd)]


# ---------------------------------------------------------------------------
# Bash — kill, git write, bind de porta, migracao de schema (regra 4).
# ---------------------------------------------------------------------------

# Toda forma conhecida de encerrar processo no Windows -- a lista curta anterior
# (`taskkill|stop-process|pkill|kill`) deixava passar 4 bypasses REPRODUZIDOS pela
# 3a auditoria (11/09), cada um matando o browser de uma peer com claim viva e
# recebendo `allow`:
#   wmic process where name=... call terminate   (e a variante `delete`)
#   pskill chrome.exe        -> `\bkill\b` nao casa: "kill" vem colado ao "s"
#   spps -Name chrome        -> alias nativo do PowerShell para Stop-Process
#   Stop-Process -Id 1234    -> casava o gatilho, mas o alvo por PID e coberto abaixo
# Gate cego e pior que gate ausente: promete proteger e libera em silencio.
# Na duvida, incluir o padrao -- falso positivo aqui custa um aviso a mais, falso
# negativo custa o trabalho de uma sessao inteira.
# --------------------------------------------------------------------------
# POSICAO DE COMANDO (achado 21/09/2026)
#
# A lista explicita de executaveis ja evitava casar `skill`/`killer_app` -- a
# palavra COLADA noutra. O que ela nao via e a palavra ISOLADA em posicao de
# ARGUMENTO: `grep -n 'kill' policy.py`, `echo "deny de kill"`,
# `git worktree add -b fix/kill-trigger`. Todos casavam, nenhum tem alvo, e
# alvo ausente cai no fail-closed `process:alvo-nao-identificado` = DENY DURO.
#
# A assimetria que o comentario original nao previu: aqui falso positivo NAO
# custa "um aviso a mais", custa a recusa de um comando de LEITURA. Medido duas
# vezes na mesma sessao, e a segunda foi o gate barrando o conserto do proprio
# gate (pelo nome do branch).
#
# Conserto: o VERBO so conta em posicao de comando (inicio, ou depois de
# `|`, `&&`, `;`, `(`, nova linha), tolerando prefixos que nao trocam o verbo
# (`sudo`, `nohup`, `env`, `do`, `then`, atribuicao `VAR=1`). Os padroes NAO
# verbais (`wmic`, CIM/WMI, `.kill()`, `.terminate()`) seguem casando em
# qualquer posicao -- nenhum deles aparece em texto inocente.
_VERBOS_KILL = r"(?:taskkill|tskill|pskill|pkill|kill|stop-process|spps)"

# Prefixos que NAO trocam o verbo: o que vem depois deles continua sendo o
# comando. `start-process` antes de `start`, e os interpretadores com a flag
# junto (`cmd /c`, `powershell -Command`) porque o verbo aninhado depois deles
# e comando de verdade -- achado 4 da auditoria adversarial.
_PREFIXOS_NEUTROS = (
    r"(?:sudo|nohup|env|time|do|then|else|xargs|exec|command|eval|wsl"
    r"|iex|invoke-expression|start-process|start"
    r"|cmd(?:\.exe)?\s+/[ck]|powershell(?:\.exe)?\s+-(?:c|command)"
    r"|pwsh|bash|sh|zsh)\s+"
)

_KILL_VERBO_EM_POSICAO = re.compile(
    # Inicio de segmento: comeco da string, separador de shell, abertura de
    # subshell -- ou `-exec`/`-execdir` do `find`, que iniciam um comando novo
    # tanto quanto um `;` (achado proprio, 21/09: `find . -exec taskkill ...`).
    # `{` e `}` sao inicio de comando tanto quanto `;`: todo o idioma
    # PowerShell de matar processo passa por bloco
    # (`... | ForEach-Object { Stop-Process ... }`, `if ($p) { ... }`).
    # Achado pelo diferencial contra 29 mil comandos reais desta maquina, nao
    # por inspecao -- nenhuma das duas auditorias tinha chegado nele.
    r"(?:^|[|;&`\n{}]|\$\(|\(|\B-execdir\b|\B-exec\b|\B-okdir\b|\B-ok\b)\s*"
    # Prefixo neutro, cada um podendo trazer as PROPRIAS flags antes do verbo:
    # `xargs -I{} taskkill`, `sudo -u x taskkill`. Sem isto o prefixo so valia
    # colado no verbo, e `xargs -I{}` virava bypass.
    r"(?:" + _PREFIXOS_NEUTROS + r"(?:[-/]\S+\s+|\{\}\s+)*)*"
    r"(?:\w+=\S*\s+)*"
    r"(?P<ancora>"
    # Caminho ate o executavel: `/c/Windows/System32/taskkill.exe //PID 1` e a
    # forma que o proprio dono usa no Git Bash, e `C:\Windows\System32\...`
    # aparece no PowerShell. Sem isto o verbo vem depois de `/` ou `\`, que nao
    # e separador -- falso negativo. Achado pelo diferencial no corpus real.
    r"(?:[A-Za-z]:)?(?:[\w.~$-]*[/\\])*"
    + _VERBOS_KILL + r"\b)",
    re.IGNORECASE,
)

# Literais entre aspas: neutralizar OU preservar, e a escolha do default e o
# ponto mais delicado deste arquivo.
#
# A 1a versao usava allowlist de EXECUCAO -- preservava o literal so depois de
# `powershell -c`/`cmd /c`. A auditoria adversarial de 21/09 abriu 6 bypasses
# nela de uma vez (`iex "taskkill ..."`, `& "taskkill" ...`,
# `$c = "taskkill"; & $c`, `powershell -Command "cmd /c taskkill"`,
# `wsl kill`, `Start-Process taskkill`): toda forma de executar string que nao
# estivesse na lista tinha o verbo APAGADO junto com as aspas. Lista de
# execucao e infinita; lista de leitura e curta.
#
# Invertido: o literal so e neutralizado quando o comando que o recebe e um
# LEITOR conhecido (grep, echo, git, ...). Qualquer outro -- inclusive
# desconhecido -- preserva o miolo. Falha para o lado de DETECTAR, que e a
# assimetria certa: falso negativo custa o processo de uma peer, falso positivo
# custa um aviso.
_LITERAL = re.compile(r"'[^']*'|\"[^\"]*\"")

_LEITORES = (
    "echo", "print", "printf", "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "findstr", "sed", "awk", "cat", "type", "head", "tail", "less", "more",
    "git", "ls", "dir", "find", "wc", "sort", "uniq", "diff", "jq",
    # `python -c` executa, sim -- mas o caso legitimo (um `-c` que so le e
    # menciona a palavra) e frequente, e o caso perigoso ja tem guarda propria
    # em `_EXEC_DE_DENTRO` (os.system/subprocess/Popen).
    "python", "python3", "py",
)
_SEPARADOR_DE_SEGMENTO = re.compile(r"[|;&`\n(]|\$\(")


def _comando_do_segmento(texto_antes: str) -> str:
    """Primeiro token do segmento de comando em que o literal esta."""
    corte = 0
    for m in _SEPARADOR_DE_SEGMENTO.finditer(texto_antes):
        corte = m.end()
    tokens = texto_antes[corte:].strip().split()
    return tokens[0].lower().lstrip("\\/.").rsplit("\\", 1)[-1] if tokens else ""
# Limitacao conhecida e assumida: `python -c "..."` NAO entra na excecao acima,
# porque o caso legitimo (um `python -c` que so le arquivo e menciona a palavra)
# e frequente e o ilegitimo e rebuscado. Para nao deixar o buraco aberto, um
# `python -c` que chama shell de dentro do codigo continua contando como kill.
_EXEC_DE_DENTRO = re.compile(
    # Casa pelo METODO, nao pelo modulo: `__import__('os').system('taskkill ...')`
    # nunca escreve o texto `os.system` e passava batido (achado proprio, 21/09).
    r"(?:\.system\(|\.popen\(|\bpopen\(|\bsubprocess\b|\bPopen\b"
    r"|\bcheck_call\b|\bcheck_output\b|\bos\.exec|\bexecv|\brun\()",
)


def _neutraliza_literais(command: str) -> str:
    """Troca o miolo dos literais por espacos, preservando offsets e tamanho."""

    def _troca(m):
        bruto = m.group(0)
        dono = _comando_do_segmento(command[: m.start()])
        # Ser leitor NAO basta: leitor executa string com frequencia
        # (`sed '1e <cmd>'`, `awk 'BEGIN{system("<cmd>")}'`,
        # `git -c alias.k='!<cmd>' k`). A 2a auditoria abriu os quatro assim.
        # O discriminante honesto nao e o nome do programa, e o MIOLO: kill de
        # verdade precisa de ALVO (`/IM x`, `/PID n`, `-Name x`, `-Id n`).
        # `grep -n 'kill'` e `git commit -m "kill switch"` nao tem alvo e
        # seguem sendo menção; `sed '1e taskkill /F /IM chrome.exe'` tem.
        if (
            dono
            and dono.removesuffix(".exe") in _LEITORES
            and _extrair_alvo_kill(bruto) is None
        ):
            return " " * len(bruto)
        # Preserva: troca so as ASPAS por `;`, mantendo o miolo e o tamanho. O
        # conteudo continua legivel E o verbo fica em inicio de segmento, que e
        # o que `_KILL_VERBO_EM_POSICAO` exige. Sem isto o verbo ficaria colado
        # numa aspa, que nao e separador de comando -- falso NEGATIVO.
        return ";" + bruto[1:-1] + ";"

    return _LITERAL.sub(_troca, command)


def _literal_carrega_comando_de_kill(command: str) -> bool:
    """Algum literal contem verbo de kill COM ALVO, isto e, um comando embutido.

    Independe de QUEM recebe o literal, e e por isso que existe: a 2a auditoria
    furou a lista de leitores por tres portas diferentes
    (`sed '1e <cmd>'`, `awk 'BEGIN{system("<cmd>")}'`,
    `git -c alias.k='!<cmd>' k`), e preservar o literal nao resolvia porque o
    verbo fica atras de `1e `, `!` ou `system(` -- nenhum e inicio de segmento.
    O discriminante e o ALVO: kill de verdade nomeia o que morre; mencao nao.
    Custo aceito: `grep "taskkill /F /IM chrome.exe" log.txt` (procurar a linha
    exata num log) vira aviso. Erra para o lado de proteger.
    """
    for m in _LITERAL.finditer(command):
        miolo = m.group(0)
        if re.search(r"\b" + _VERBOS_KILL + r"\b", miolo, re.IGNORECASE) and (
            _extrair_alvo_kill(miolo) is not None
        ):
            return True
    return False


def _tem_gatilho_kill(command: str) -> bool:
    """Ha verbo de kill em posicao de comando, ou padrao nao verbal em qualquer lugar."""
    if _KILL_NAO_VERBAL.search(command):
        return True
    if _literal_carrega_comando_de_kill(command):
        return True
    if _EXEC_DE_DENTRO.search(command) and re.search(
        r"\b" + _VERBOS_KILL + r"\b", command, re.IGNORECASE
    ):
        return True
    return bool(_KILL_VERBO_EM_POSICAO.search(_neutraliza_literais(command)))


_KILL_NAO_VERBAL = re.compile(
    r"(\bwmic\b[^|;&]*\b(terminate|delete)\b"  # wmic ... call terminate | delete
    # CIM/WMI moderno — achado ALTA da 5a auditoria (14/09). `wmic` esta
    # DEPRECADO no Windows 11; o caminho atual e
    # `Get-CimInstance Win32_Process | Invoke-CimMethod -MethodName Terminate`
    # (ou o `Get-WmiObject ... .Terminate()` antigo). Nao e exotico: o proprio
    # projeto usa `Get-CimInstance Win32_Process` como jeito padrao de CONTAR
    # processo, entao era o idioma mais provavel de aparecer num kill.
    r"|\binvoke-cimmethod\b[^|;&]*\bterminate\b"
    r"|\bget-(cim|wmi)(instance|object)\b[^|;&]*\bterminate\b"
    r"|\.terminate\(\)"
    r"|\.kill\(\)"  # (Get-Process x).Kill() — mesma familia, sem verbo de kill
    r")",
    re.IGNORECASE,
)

_KILL_TARGET_PATTERNS = (
    # `-Filter "Name='chrome.exe'"` do CIM/WMI: o alvo vive dentro do filtro, não
    # numa flag. Sem isto o kill por CIM caía no fail-closed genérico — protegido,
    # mas sem poder dizer QUEM perde o processo, que é metade do valor do aviso.
    re.compile(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    # Entre aspas PRIMEIRO: caminho de programa tem espaco
    # (`/IM "C:\Program Files\Google\Chrome\chrome.exe"`), e o padrao sem aspas
    # cortava no espaco, devolvendo `c:\program` -- id que nao casa com claim
    # nenhum, logo kill liberado. Achado da 2a auditoria, 21/09.
    re.compile(r"/im\s+\"([^\"]+)\"", re.IGNORECASE),
    re.compile(r"-name\s+\"([^\"]+)\"", re.IGNORECASE),
    re.compile(r"/im\s+\"?([^\"\s]+)\"?", re.IGNORECASE),
    re.compile(r"-name\s+\"?([^\"\s,]+)\"?", re.IGNORECASE),
    re.compile(r"/pid\s+(\d+)", re.IGNORECASE),
    re.compile(r"-id\s+(\d+)", re.IGNORECASE),
    re.compile(r"\bpkill\b\s+(?:-\w+\s+)*\"?([^\"\s]+)\"?", re.IGNORECASE),
    re.compile(r"\bkill\b\s+(?:-\d+\s+)?(\d+)", re.IGNORECASE),
)

_BROWSER_NAMES = ("chrome", "chromium", "msedge", "edge", "firefox", "brave")

# Grafias reais mais comuns dos executaveis de browser conhecidos (com e sem
# `.exe`) — usado so para EXPANDIR curinga (ver `_candidatos_wildcard`
# abaixo), nunca para o caso sem curinga (que continua preservando o texto
# literal do comando, igual antes — mudar isso quebraria o casamento exato
# que `claims.owner_of()` faz contra a grafia real do claim).
_BROWSER_EXECUTAVEIS = tuple(
    sorted({variante for nome in _BROWSER_NAMES for variante in (nome, nome + ".exe")})
)


# Pontuacao de fecho que gruda no alvo quando o comando vem aninhado dentro de
# um literal (`...system('taskkill /F /IM chrome.exe')` devolvia `chrome.exe')`).
# Id com lixo na ponta nao casa com claim nenhum, e `claims.owner_of()` compara
# string EXATA -- ou seja, o kill sairia LIBERADO. E o mesmo modo de falha do
# curinga documentado em `_candidatos_wildcard`, por outra porta. Achado proprio
# em 21/09, ao medir o conserto de posicao.
_LIXO_NA_BORDA = "'\"`)]},;:"


def _normaliza_alvo(bruto: str) -> Optional[str]:
    """Deixa o alvo na forma que `claims.owner_of()` compara: nome do processo.

    A comparacao com o claim e de string EXATA, entao qualquer sujeira grudada
    faz o id nao casar e o kill sair LIBERADO -- detectar sem identificar nao
    protege. Tres formas medidas em 21/09, todas pre-existentes ao conserto de
    posicao: `chrome.exe>nul` (redirecionamento do cmd colado no nome),
    `chrome.exe;stop-process` (segundo comando sem espaco antes do `;`) e
    `c:\\program` (caminho com espaco cortado no meio).
    """
    alvo = bruto.strip().strip(_LIXO_NA_BORDA)
    # Escape que o PROPRIO shell come antes de executar: `ch^rome.exe` no
    # cmd.exe e `chro`me.exe` no PowerShell chegam ao SO como `chrome.exe`.
    # Provado em runtime na 3a auditoria (`cmd //c "echo ch^rome.exe"` imprime
    # `chrome.exe`). Nenhum nome de processo real usa esses caracteres, entao
    # remover e seguro -- e nao remover era um kill de peer saindo LIBERADO.
    alvo = alvo.replace("^", "").replace("`", "")
    # operador de shell colado no nome: corta no primeiro
    alvo = re.split(r"[>|<&;]", alvo, maxsplit=1)[0]
    # caminho completo -> nome do executavel (o claim guarda o nome, nao o path)
    alvo = alvo.replace("/", "\\").rsplit("\\", 1)[-1]
    return alvo.strip().strip(_LIXO_NA_BORDA) or None


_VERBO_EM_QUALQUER_POSICAO = re.compile(r"\b" + _VERBOS_KILL + r"\b", re.IGNORECASE)


class _Ancora:
    """Posicao do verbo que ancora a extracao do alvo. Só `start()` importa."""

    __slots__ = ("_pos",)

    def __init__(self, pos: int):
        self._pos = pos

    def start(self) -> int:
        return self._pos


def _ancora_do_verbo(command: str):
    """Onde comeca o verbo que MANDA no comando -- nao a primeira palavra parecida.

    A 6a auditoria achou o defeito que eu mesmo criei ao prender a extracao ao
    segmento: a ancora era a primeira ocorrencia da palavra em QUALQUER lugar,
    entao `echo "kill 99 please"; taskkill /F /IM chrome.exe` ancorava no texto
    decorativo. O segmento virava o do `echo`, o taskkill real sumia, e o gate
    devolvia `process:99` -- id que nao casa com claim nenhum, ou seja LIBERA o
    kill do chrome de uma peer. Ancorar no verbo em POSICAO DE COMANDO, sobre o
    texto com os literais de leitor neutralizados, e o que distingue os dois.

    O fallback para "qualquer posicao" cobre o caso em que o gatilho veio de um
    literal que carrega comando (`sed '1e taskkill ...'`), onde o verbo
    legitimamente nao esta em posicao de comando no texto neutralizado.
    """
    m = _KILL_VERBO_EM_POSICAO.search(_neutraliza_literais(command))
    if m:
        return _Ancora(m.start("ancora"))
    return _VERBO_EM_QUALQUER_POSICAO.search(command)

# Separadores de COMANDO. O pipe simples NAO entra: `Get-Process chrome |
# Stop-Process` e um comando so, e o alvo mora do lado esquerdo do pipe.
# Tetos do caminho quente (RNF-04, p95 < 150 ms). Comando com dezenas de
# verbos so aparece em heredoc que ESCREVE teste; oito ancoras cobrem
# qualquer linha de comando real, e a janela cobre a selecao que alimenta
# o kill.
_ALVO_INDETERMINADO = "alvo-nao-identificado"
_MAX_ANCORAS = 8
_JANELA_ALVO_A_ESQUERDA = 600

_SEP_ENTRE_COMANDOS = re.compile(r"&&|\|\||[;&\n]")


def _limites_do_segmento(command: str, pos: int):
    """(inicio, fim) do segmento de comando que contem `pos`."""
    inicio = 0
    for m in _SEP_ENTRE_COMANDOS.finditer(command, 0, pos):
        inicio = m.end()
    m_fim = _SEP_ENTRE_COMANDOS.search(command, pos)
    return inicio, (m_fim.start() if m_fim else len(command))


def _segmento_do_verbo(command: str, pos_verbo: int) -> str:
    """Trecho do comando que contem o verbo, entre separadores de comando.

    Existe por causa da 4a auditoria: o fallback "procura no comando inteiro"
    reabria o decoy no idioma `Get-Process X | Stop-Process`, onde o trecho
    DEPOIS do verbo nao tem flag de alvo nenhuma. Com um `/im decoy.exe`
    plantado num `echo` anterior, o gate passava a acusar o processo errado --
    `browser:firefox.exe` enquanto o chrome da peer morria, e o pior e que
    parecia conferido. Prender a busca ao segmento mantem o CIM funcionando
    (o alvo vem antes do verbo, mas no MESMO segmento) e corta o decoy, que
    por definicao mora em outro comando.
    """
    inicio, fim = _limites_do_segmento(command, pos_verbo)
    return command[inicio:fim]


@functools.lru_cache(maxsize=64)
def _mapa_de_segmentos(command: str):
    """[(inicio, fim, eh_leitor)] de cada segmento, calculado UMA vez por comando.

    Sem isto, `_em_segmento_de_leitor` reprocessava todo o prefixo a cada match
    e o custo virava quadratico -- o p95 do caminho quente (RNF-04) estourou na
    primeira versao deste filtro.
    """
    mapa, ini = [], 0
    for m in _SEP_ENTRE_COMANDOS.finditer(command):
        mapa.append((ini, m.start()))
        ini = m.end()
    mapa.append((ini, len(command)))
    saida = []
    for a, b in mapa:
        trecho = command[a:b]
        tokens = trecho.strip().split()
        dono = tokens[0].lower().lstrip("\\/.").rsplit("\\", 1)[-1] if tokens else ""
        eh_leitor = bool(dono) and dono.removesuffix(".exe") in _LEITORES
        # Leitor que chama shell de dentro (`python -c "...os.system(...)"`) nao
        # e leitor: e execucao. Mesma guarda que o gatilho ja usava.
        if eh_leitor and _EXEC_DE_DENTRO.search(trecho):
            eh_leitor = False
        saida.append((a, b, eh_leitor))
    return tuple(saida)


def _em_segmento_de_leitor(command: str, pos: int) -> bool:
    """A posicao esta num segmento comandado por um LEITOR (echo, grep, git...)?

    A 7a auditoria achou tres bypasses com a mesma raiz: a busca de alvo lia o
    texto CRU, entao `echo "/IM chrome.exe"; Stop-Process -Id $x` e
    `echo "Name='decoy.exe'" ; Invoke-CimMethod ... Terminate` faziam o gate
    ACUSAR O PROCESSO ERRADO -- pior que nao identificar, porque um id que
    ninguem reivindicou vira allow silencioso. O gatilho ja ignorava esses
    literais; a extracao de alvo, nao. Agora as duas usam o mesmo criterio.
    """
    for ini, fim, eh_leitor in _mapa_de_segmentos(command):
        if ini <= pos < fim:
            return eh_leitor
    return False


def _primeiro_alvo(command: str, ini: int = 0, fim: Optional[int] = None) -> Optional[str]:
    """Primeiro alvo dos padroes na faixa, ignorando o que esta em segmento de leitor."""
    if fim is None:
        fim = len(command)
    for pat in _KILL_TARGET_PATTERNS:
        for m in pat.finditer(command, ini, fim):
            if not _em_segmento_de_leitor(command, m.start()):
                return _normaliza_alvo(m.group(1))
    return None


def _extrair_alvo_kill(command: str) -> Optional[str]:
    """Alvo do kill, preferindo o que vem DEPOIS do verbo.

    DECOY (3a auditoria, 21/09): buscar no comando inteiro fazia
    `echo teste /im "decoy.exe" ; taskkill /F /IM chrome.exe` devolver
    `decoy.exe`. O gate entao protegia o processo ERRADO -- pior que nao
    proteger, porque `owner_of("process:decoy.exe")` da None e o kill do chrome
    da peer sai liberado parecendo conferido.

    O fallback para o comando inteiro nao e preguica: no idioma real do CIM
    (`Get-CimInstance -Filter "Name='chrome.exe'" | ForEach { Stop-Process }`)
    o alvo vem ANTES do verbo, e cortar no verbo viraria falso negativo. Tenta
    depois do verbo; so entao o texto todo.
    """
    m_verbo = _ancora_do_verbo(command)
    if m_verbo:
        # Os DOIS trechos ficam presos ao segmento do verbo. A 1a versao deste
        # conserto limitou so o fallback e deixou o caminho primario indo ate o
        # fim da string -- entao o decoy mudou de lado e continuou funcionando
        # (`taskkill /F /PID 1234 & echo "/im chrome.exe"` devolvia
        # `browser:chrome.exe`). Pior: o comentario afirmava a protecao que o
        # caminho primario nao tinha. Achado da 5a auditoria; simetrico exato do
        # decoy anterior, e a licao e que blindar UM caminho nao blinda a funcao.
        ini, fim = _limites_do_segmento(command, m_verbo.start())
        trechos = [
            command[m_verbo.start():fim],  # depois do verbo, dentro do segmento
            command[ini:fim],              # segmento inteiro (CIM poe o alvo antes)
        ]
    else:
        trechos = [command]
    for trecho in trechos:
        for pat in _KILL_TARGET_PATTERNS:
            m = pat.search(trecho)
            if m:
                return _normaliza_alvo(m.group(1))
    return None


def _candidatos_wildcard(alvo_lower: str) -> list:
    """Expande um alvo de kill com curinga (`*`/`?`) nas grafias reais mais
    comuns de browser conhecido que ele poderia estar mirando.

    Achado #6 (3a auditoria), ALTA: `Stop-Process -Name chrom* -Force` e
    `taskkill /F /IM chrom*` matam o MESMO chrome.exe de uma peer viva, mas
    o codigo original montava o id do texto cru do alvo (`process:chrom*`)
    — `claims.owner_of()` faz correspondencia EXATA de string (claims.py
    567-575), entao o id com curinga nunca bate com o claim real
    (`browser:chrome` ou `browser:chrome.exe`), e o kill sai liberado.

    So cobre o conjunto FECHADO de browsers conhecidos (`_BROWSER_NAMES`) —
    e a unica lista que da para expandir sem I/O (consultar processos reais
    do SO esta fora do escopo deste modulo puro, RNF-04). Kill de processo
    GENERICO com curinga (ex. `taskkill /IM note*`) continua sem protecao —
    nao ha lista fechada de "todo processo possivel" para expandir contra;
    fechar esse caso exigiria `claims.owner_of()` (ou o entrypoint) aceitar
    correspondencia por padrao/fnmatch contra os claims REALMENTE
    registrados, uma mudanca fora deste arquivo. Indirecao por variavel de
    ambiente (`%BROWSER%`, `$env:BROWSER_PROC`) e OUTRO caso, tambem fora do
    alcance de uma funcao pura: precisa ler o valor real da env var, que e
    I/O — teria que entrar no hook (`coord_pre_bash.py`), nao aqui.

    Devolve lista vazia quando `alvo_lower` nao tem curinga, ou quando tem
    curinga mas nao bate com nenhum browser conhecido (nesses casos o
    chamador cai no comportamento antigo, sem regressao).
    """
    if "*" not in alvo_lower and "?" not in alvo_lower:
        return []
    partes = []
    for ch in alvo_lower:
        if ch == "*":
            partes.append(".*")
        elif ch == "?":
            partes.append(".")
        else:
            partes.append(re.escape(ch))
    padrao = re.compile("^" + "".join(partes) + "$")
    return [nome for nome in _BROWSER_EXECUTAVEIS if padrao.match(nome)]


# `Get-Process chrome | Stop-Process`, `(Get-Process chrome).Kill()` — o idioma
# mais comum de PowerShell para matar processo por nome, e o que a 5a auditoria
# (14/09) reproduziu saindo LIBERADO: `_extrair_alvo_kill` nao achava alvo, o
# recurso virava `process:desconhecido`, esse id nunca casa com o claim real
# (`browser:chrome`), `owner_of` devolvia None = "livre" e o kill passava. O nome
# esta no `Get-Process`, nao no verbo de kill — por isso e extraido a parte.
_GET_PROCESS_NOME = re.compile(
    r"\bget-process\b\s+(?:-name\s+)?[\"']?([\w.*?-]+)[\"']?", re.IGNORECASE
)


def _alvos_de_kill(command: str):
    """TODOS os alvos do comando, um por verbo em posicao de comando.

    Devolver um alvo so era a raiz de tres defeitos diferentes:
      - `taskkill /F /IM a.exe & taskkill /F /IM b.exe` checava so o `a.exe`,
        e o `b.exe` morria sem passar por claim nenhum (4a auditoria);
      - `taskkill /F /IM a.exe /IM b.exe`, idem;
      - `echo "kill 99 please"; taskkill /F /IM chrome.exe` ancorava no texto
        decorativo e o chrome real ficava INVISIVEL (6a auditoria).
    Coletar todos resolve os tres de uma vez e erra para o lado seguro: um alvo
    a mais custa uma checagem de claim, um alvo a menos custa o processo de uma
    peer.
    """
    neutro = _neutraliza_literais(command)
    todas = [m.start("ancora") for m in _KILL_VERBO_EM_POSICAO.finditer(neutro)]
    posicoes = todas[:_MAX_ANCORAS]
    # Teto e limite de CUSTO, nunca licenca para ignorar em silencio: o que
    # passa dele vira o sentinela de fail-closed. Antes, o 9o e o 10o kill de um
    # encadeamento simplesmente nao viravam recurso, logo nao passavam por
    # `decide()` -- allow por omissao total (7a auditoria).
    excedeu = len(todas) > _MAX_ANCORAS
    if not posicoes:
        # Gatilho veio de literal que carrega comando (`sed '1e taskkill ...'`),
        # onde o verbo nao esta em posicao de comando no texto neutralizado.
        m = _VERBO_EM_QUALQUER_POSICAO.search(command)
        if m:
            posicoes = [m.start()]
        else:
            # Kill NAO VERBAL (`Invoke-CimMethod ... Terminate`, `wmic ... call
            # terminate`, `.Kill()`): nao existe verbo para ancorar, e o alvo
            # mora num `-Filter "Name='...'"`. O comando inteiro e o escopo, mas
            # segmento de leitor continua fora -- o comentario antigo dizia que
            # aqui nao havia decoy possivel, e a 7a auditoria provou o contrario
            # com um `echo` antes do `Invoke-CimMethod`.
            alvo = _primeiro_alvo(command) or _alvo_a_esquerda_do_verbo(command, len(command))
            return [alvo] if alvo else []

    alvos = []
    for pos in posicoes:
        ini, fim = _limites_do_segmento(command, pos)
        alvo = _primeiro_alvo(command, pos, fim) or _primeiro_alvo(command, ini, fim)
        if alvo is None:
            alvo = _alvo_a_esquerda_do_verbo(command, pos)
        if alvo and alvo not in alvos:
            alvos.append(alvo)
    if excedeu:
        alvos.append(_ALVO_INDETERMINADO)
    return alvos


def _alvo_a_esquerda_do_verbo(command: str, pos_verbo: int):
    """Alvo declarado ANTES do verbo, pela ocorrencia mais proxima dele.

    O idioma real de limpeza do chrome orfao do Playwright separa a selecao do
    kill por `;`:

        $p = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
             Where-Object { ... }; $p | ForEach-Object { Stop-Process -Id $_.ProcessId }

    Prender a busca ao segmento do verbo quebrava isso (virava deny) -- medido
    em 6 comandos genuinos do historico desta maquina. Varrer o texto inteiro
    reabria o decoy. A regra que concilia os dois e a PROXIMIDADE: vale o alvo
    mais proximo a esquerda do verbo, porque decoy plantado num `echo` anterior
    fica sempre mais longe que a selecao legitima que alimenta o kill.
    """
    # Janela, nao o prefixo inteiro: a selecao que alimenta o kill fica a
    # algumas centenas de caracteres dele, e varrer 24 KB de heredoc com 9
    # padroes por verbo estourou o p95 do caminho quente (RNF-04) -- medido em
    # 220 ms contra o teto de 150.
    inicio = max(0, pos_verbo - _JANELA_ALVO_A_ESQUERDA)
    melhor = None
    for pat in (_GET_PROCESS_NOME,) + _KILL_TARGET_PATTERNS:
        for m in pat.finditer(command, inicio, pos_verbo):
            # Um `echo` plantado entre a selecao real e o verbo fica MAIS PROXIMO
            # que ela, entao a regra de proximidade viraria arma sem este filtro.
            if _em_segmento_de_leitor(command, m.start()):
                continue
            if melhor is None or m.start() > melhor.start():
                melhor = m
    return _normaliza_alvo(melhor.group(1)) if melhor else None


def _recursos_do_alvo(alvo: str):
    if alvo == _ALVO_INDETERMINADO:
        return [Resource(kind="process", id=f"process:{_ALVO_INDETERMINADO}", action="kill")]
    alvo_lower = alvo.lower()
    curingas = _candidatos_wildcard(alvo_lower)
    if curingas:
        return [
            Resource(kind="browser", id=f"browser:{nome}", action="kill")
            for nome in curingas
        ]
    if any(nome in alvo_lower for nome in _BROWSER_NAMES):
        return [Resource(kind="browser", id=f"browser:{alvo_lower}", action="kill")]
    return [Resource(kind="process", id=f"process:{alvo_lower}", action="kill")]


def _detectar_kill(command: str):
    if not _tem_gatilho_kill(command):
        return []
    alvos = _alvos_de_kill(command)
    if alvos:
        recursos, vistos = [], set()
        for alvo in alvos:
            for r in _recursos_do_alvo(alvo):
                if r.id not in vistos:
                    vistos.add(r.id)
                    recursos.append(r)
        return recursos
    # FAIL-CLOSED. Antes isto virava `process:desconhecido`, um id que nao
    # casa com claim nenhum — ou seja, a forma mais facil de matar processo
    # de peer viva era escrever o comando de um jeito que o parser nao
    # entendesse. Kill e irreversivel: quando nao da para dizer O QUE morre,
    # a resposta honesta e recusar e mandar perguntar, nao liberar por
    # ignorancia. O id sentinela e reconhecido pela politica.
    return [Resource(kind="process", id="process:alvo-nao-identificado", action="kill")]


_GIT_TRIGGER = re.compile(r"\bgit\b")
# Flags globais do git entre `git` e o verbo/subcomando — achado #5 (3a
# auditoria), ALTA: o regex original so tolerava exatamente `-C <path>`
# entre "git" e o verbo; QUALQUER outra flag global (`--no-pager`,
# `--git-dir=`, `--work-tree=`, `-p`, etc.) quebrava a adjacencia exigida e
# `classify()` devolvia lista vazia — o deny de commit/push com peer viva
# (o UNICO outro deny irreversivel da spec alem do kill) nunca disparava.
# `--no-pager` em particular e habito comum (evita o pager travar hooks em
# contexto nao-interativo, ver pesquisa_hooks_riscos.md).
#
# Generaliza para qualquer token que comece com `-`/`--` (cobre flags de
# valor colado com `=`, tipo `--git-dir=.git`), MAIS o caso de duas palavras
# separadas por espaco de `-C`/`-c` (as unicas flags globais comuns do git
# cujo valor nao vem colado). Residual conhecido: uma flag de valor separado
# DIFERENTE de -C/-c (rara) ainda quebraria a deteccao — mais estreito que
# antes (so -C), nunca mais largo.
_GIT_GLOBAL_SKIP = r"(?:\s+-[Cc]\s+\"?[^\"\s]+\"?|\s+-{1,2}[^\s\"]+)"
_GIT_C_FLAG = re.compile(
    rf"\bgit\b(?:{_GIT_GLOBAL_SKIP})*\s+-C\s+\"?([^\"\s]+)\"?", re.IGNORECASE
)
_GIT_WRITE_ACTIONS = ("commit", "push", "reset", "checkout")


_GIT_FLAG_COM_VALOR = ("-m", "--message", "-c", "--author", "--date", "-C", "--file", "-F")


def pathspecs_de_commit(command: str) -> list:
    """Caminhos explicitos de um `git commit <caminho> [...]`, ou [] se nao houver.

    Por que isto existe (ensaio T-013, 12/09): `git commit <caminho> -m "..."`
    commita **so** aquele caminho e IGNORA o indice -- medido com `A b.txt`
    staged, `git commit a.txt` levou so o a.txt e deixou o b.txt intacto. Em
    arvore compartilhada isso e seguranca por CONSTRUCAO, diferente de "conferi
    o indice e estava limpo", que expira em segundos com peer viva (a peer pode
    dar `add` entre o check do hook e o commit).

    Devolve [] quando o commit e sem pathspec (`git commit -m`, `git commit -am`)
    -- esse e o caso perigoso, porque leva o que estiver no indice, que e
    compartilhado. Tambem devolve [] para `-a`/`--all` mesmo com caminho junto:
    o `-a` varre tudo que esta tracked, e a presenca do pathspec nao desfaz isso.
    """
    m = re.search(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b(.*)", command, re.IGNORECASE)
    if not m:
        return []
    resto = m.group(1)
    # corta em separador de comando: `&&`, `;`, `|`
    resto = re.split(r"&&|\|\||[;|]", resto)[0]

    # `split()` cru quebraria `-m "trabalho da B"` em tres tokens e as palavras
    # da mensagem virariam "caminhos". `posix=False` mantem as aspas no token
    # (removidas abaixo) e nao trata `\` como escape, que e o que se quer em
    # caminho do Windows.
    try:
        import shlex

        tokens = shlex.split(resto, posix=False)
    except ValueError:  # aspas nao fechadas: melhor tratar como sem pathspec
        return []
    caminhos = []
    pular = False
    for tok in tokens:
        if pular:
            pular = False
            continue
        if tok.startswith("-"):
            base = tok.split("=", 1)[0].lower()
            if base in _GIT_FLAG_COM_VALOR and "=" not in tok:
                pular = True
            # `-a`, `-am`, `--all`: indice implicito, pathspec nao salva
            if base in ("-a", "--all") or (
                re.fullmatch(r"-[a-z]+", base) and "a" in base[1:]
            ):
                return []
            continue
        limpo = tok.strip("\"'")
        if limpo:
            caminhos.append(limpo)
    return caminhos


def _detectar_git(command: str, cwd: str):
    if not _GIT_TRIGGER.search(command):
        return []

    m = _GIT_C_FLAG.search(command)
    repo_path = m.group(1) if m else cwd
    if not repo_path:
        return []

    id_, path_exibicao = _path_to_id("git", repo_path, cwd)
    resources = []
    for action in _GIT_WRITE_ACTIONS:
        padrao = rf"\bgit\b(?:{_GIT_GLOBAL_SKIP})*\s+{action}\b"
        if re.search(padrao, command, re.IGNORECASE):
            resources.append(
                Resource(kind="git", id=id_, path=path_exibicao, action=action)
            )
    return resources


_PORT_PATTERNS = (
    re.compile(r"--port[=\s]+(\d{2,5})", re.IGNORECASE),
    re.compile(r"\bport\s*=\s*(\d{2,5})\b", re.IGNORECASE),
    re.compile(r"(?<!\S)-p\s+(\d{2,5})\b"),
)
_HTTP_SERVER_PORT = re.compile(r"http\.server\s+(\d{2,5})\b", re.IGNORECASE)


def _detectar_portas(command: str):
    portas = set()
    for pat in _PORT_PATTERNS:
        for m in pat.finditer(command):
            portas.add(int(m.group(1)))
    m = _HTTP_SERVER_PORT.search(command)
    if m:
        portas.add(int(m.group(1)))
    return sorted(portas)


def _detectar_bind(command: str, cwd: str):
    portas = _detectar_portas(command)
    if not portas:
        return []
    resources = []
    reponame = _repo_short_name(cwd) if cwd else None
    for n in portas:
        resources.append(Resource(kind="port", id=f"port:{n}", action="bind"))
        if reponame:
            resources.append(
                Resource(kind="server", id=f"server:{reponame}:{n}", action="bind")
            )
    return resources


_MIGRATION_TRIGGER = re.compile(
    r"(prisma\s+migrate|\balembic\b|\bmigrate\s+deploy\b)", re.IGNORECASE
)


def _detectar_migracao(command: str, cwd: str):
    if not _MIGRATION_TRIGGER.search(command):
        return []
    reponame = _repo_short_name(cwd) if cwd else "desconhecido"
    return [Resource(kind="db", id=f"db:{reponame}:migrations", action="migrate")]


# ---------------------------------------------------------------------------
# cd encadeado no MESMO comando Bash (achado #3, 3a auditoria, ALTA).
# ---------------------------------------------------------------------------

# `cd <dir> && ...` (ou `;`) no INICIO do comando muda o diretorio de
# execucao real ANTES do resto do comando rodar — mas o `cwd` do payload do
# hook e o cwd da SESSAO (onde ela estava quando disparou o Bash), nunca
# atualizado por um `cd` interno ao comando. Sem isto, `cd C:\dev\repo-real
# && git commit` classificava o commit no repo ERRADO (o cwd original da
# sessao), e o deny de peer-viva-no-mesmo-repo (git/bind/migracao, os 3 usos
# de `cwd` abaixo) nunca disparava para o repo onde o commit de fato
# acontece. So reconhece `cd` (cmd.exe e Git Bash) em cadeia no INICIO do
# comando; nao tenta entender `if`/subshell/`$(...)` nem `Set-Location` do
# PowerShell — quando nao acha o padrao, devolve o `cwd` original (mesmo
# comportamento de hoje).
# So reconhecia `cd <dir> &&` colado no INICIO do comando. O ENSAIO com sessao
# real (12/09) mostrou o buraco: num bloco de shell de VARIAS LINHAS -- a forma
# mais comum -- o `cd` fica numa linha propria, invisivel para o regex antigo, e
# todo caminho relativo era resolvido contra o cwd da SESSAO. Medido: 3 claims
# sobre `dev\cc-coord\alvo.txt` (arquivo que nao existe) enquanto o arquivo
# real, em outro diretorio, nao tinha claim nenhum. Protege fantasma e deixa o
# alvo aberto -- pior que nao ter gate.
_CD_SEG = re.compile(r"^\s*cd(?:\s+/d)?(?:\s+(.*))?$", re.IGNORECASE)

# `cd "$TD"`, `cd %USERPROFILE%`, `cd `pwd``: o destino so existe em tempo de
# execucao. Tratar `$TD` como nome de pasta produzia `<cwd>\$TD`, um caminho
# inventado.
#
# `~` NAO entra aqui, e isso e uma correcao de um erro meu que o ensaio pegou:
# so vale como HOME quando e o PRIMEIRO caractere (tratado a parte, abaixo). No
# meio do caminho e caractere legitimo do nome curto 8.3 do Windows --
# `C:\Users\VINICI~1\AppData\...` e justamente o caminho do scratchpad desta
# maquina, entao marcar `~` como nao-literal cegava o gate no diretorio mais
# usado da sessao.
_NAO_LITERAL = re.compile(r"[$`%]")


def _tem_drive(texto_barras: str) -> bool:
    return len(texto_barras) >= 2 and texto_barras[1] == ":" and texto_barras[0].isalpha()


def _e_absoluto(caminho: str) -> bool:
    barras = caminho.replace("\\", "/")
    return _tem_drive(barras) or barras.startswith("/")


def _segmentos_ordenados(command: str) -> list:
    """Segmentos na ordem de execucao. Diferente de `_segmentos_bash`, quebra
    tambem por QUEBRA DE LINHA -- sem isso o `cd` de um bloco multilinha some."""
    return [s for s in re.split(r"&&|\|\||[;|\r\n]", command) if s.strip()]


def _efetivo_cwd(command: str, cwd: str):
    """cwd real de execucao depois dos `cd` do comando.

    Devolve **None** quando o comando muda para um diretorio que so se conhece
    executando (`cd "$TD"`, `cd %TEMP%`, `cd ~`), ou faz `cd` sem argumento
    (= HOME no bash). Quem chama tem de tratar None como "nao sei onde isto
    roda" em vez de assumir o cwd da sessao.

    Nao tenta entender `if`/subshell/`$(...)`/`pushd` nem `Set-Location` do
    PowerShell; quando nao acha `cd` nenhum, devolve o `cwd` recebido.
    """
    atual = cwd or ""
    for seg in _segmentos_ordenados(command):
        m = _CD_SEG.match(seg.strip())
        if not m:
            continue
        alvo = (m.group(1) or "").strip()
        if not alvo:
            return None
        if len(alvo) >= 2 and alvo[0] in "\"'" and alvo[-1] == alvo[0]:
            alvo = alvo[1:-1]
        if not alvo or _NAO_LITERAL.search(alvo) or alvo.startswith("~"):
            return None
        if _e_absoluto(alvo):
            atual = alvo
        elif atual:
            atual = atual.rstrip("\\/") + "\\" + alvo
        else:
            atual = alvo
    return atual


# ---------------------------------------------------------------------------
# Escrita de arquivo via Bash (achado #4, 3a auditoria, ALTA).
# ---------------------------------------------------------------------------

# `_classify_bash` nunca detectava escrita de arquivo — so Edit/Write/
# NotebookEdit (via `coord_pre_write.py`, matcher separado no settings.json)
# geravam recurso kind="file". Qualquer sessao usando Bash para tocar o
# MESMO arquivo que uma peer esta editando (redirecionamento, `sed -i`,
# cmdlets do PowerShell) passava batido: `classify()` devolvia lista vazia,
# entao nenhum claim/policy era consultado — nao so "sem deny", ausencia
# TOTAL de sinal (nem o warn informativo que o design promete).
#
# Cobre as formas EXPLICITAS que a auditoria reproduziu contra um claim vivo
# e confirmou bypass (redirecionamento `>`/`>>`, `sed -i`, `Set-Content`/
# `Add-Content`/`Out-File` do PowerShell) mais `tee` (mesma familia). NAO
# cobre `python -c "...open(...,'w')..."` nem qualquer escrita embutida em
# codigo arbitrario passado por `-c`/heredoc — isso exigiria interpretar
# codigo de uma linguagem qualquer, o que nao da para fazer com regex de
# forma confiavel (alto risco de falso positivo/negativo); fica como lacuna
# conhecida, documentada aqui em vez de fingida coberta.
#
# Sempre lines=None (arquivo inteiro): nenhuma das formas abaixo da para
# saber a FAIXA tocada sem abrir o arquivo (fora do escopo de uma funcao
# pura) — mesma degradacao explicita que Write ja usa.


def _segmentos_bash(command: str) -> list:
    """Quebra o comando em pedacos por `&&`, `||`, `;`, `|` — heuristica
    simples (nao entende aspas/subshell), suficiente para localizar em qual
    "trecho" um gatilho (`sed`, cmdlet do PowerShell) aparece."""
    return [s for s in re.split(r"&&|\|\||[;|]", command) if s.strip()]


# ---------------------------------------------------------------------------
# Sanidade do alvo (T-024, AC-019). Medido no uso real: 925 de 6.737 ids
# (13,7%) nao eram caminho -- eram pedaco do proprio comando. Oito chegaram a
# `os.open` e voltaram `[Errno 22] Invalid argument`; dois geraram disputa de
# claim contra recurso inexistente. Ruido que ensina a ignorar o aviso e como
# um gate morre.
#
# O filtro roda ANTES de virar recurso e so olha o TEXTO do alvo (nao toca o
# disco - o caminho quente do RNF-04 nao paga I/O por isto). Cada regra tem
# teste proprio e, junto, um controle negativo com caminhos legitimos: um
# filtro que rejeita demais troca ruido por CEGUEIRA, defeito pior do que o
# que ele conserta.
# ---------------------------------------------------------------------------

# CADA regra abaixo foi medida contra DOIS corpora antes de entrar (17/09):
#   - 736 alvos que o classificador ja produziu e que NAO existem em disco
#     (fragmentos reais, colhidos do events.log de producao);
#   - 129.562 arquivos que EXISTEM nesta maquina (vault, dev, .claude,
#     OneDrive) -- o lado que nao pode ser cegado.
# Resultado do conjunto: pega 309/736 fragmentos e cega 0 de 129.562 reais.
#
# A PRIMEIRA versao desta funcao foi reprovada por auditoria adversarial
# exatamente aqui: ela tinha regras "espertas" (parentese + `;,=`, chamada
# `\w(`, teto de 260 chars) que pareciam certas e cegavam 5,15% dos arquivos
# reais -- inclusive o vault inteiro, que nomeia nota como
# `Plano - portfolio GitHub (plano completo, 2026-08-25).md`. Medidas:
#   parentese+[;,=]  53 fragmentos, 340 arquivos reais cegados
#   chamada `\w(`    68 fragmentos, 622 arquivos reais cegados
#   len > 260         0 fragmentos, 7.278 arquivos reais cegados
# Trocar ruido por cegueira e o defeito PIOR -- por isso as tres sairam.
# Regra para mexer aqui: rode tools/avaliar_filtro.py e nao aceite nenhuma
# regra que cegue arquivo real. Intuicao sobre "cara de codigo" nao passa.
_PROIBIDOS_WIN = set('<>"|?*')
_SEPARADOR_PATH = re.compile(r"[\\/]+")
# `$VAR`, `${VAR}`, `%VAR%` ocupando um SEGMENTO INTEIRO do caminho: variavel
# que o shell expandiria e o classificador nao. Casar em qualquer posicao (e
# nao no segmento inteiro) cegaria `SG$A Rateio_Chile.xlsx` e `~$planilha.xlsx`
# -- nomes reais de planilha nesta maquina. Medido: 491 ocorrencias no log de
# producao (`$WORK`, `$SP`, `$COPY`, `$SCRATCH`), 0 arquivos reais cegados.
_SEGMENTO_VARIAVEL = re.compile(r"\$\{?[A-Za-z_]\w*\}?|%[A-Za-z_]\w*%")
# Rede final contra bloco de codigo inteiro capturado como alvo. Alto de
# proposito: o limite de MAX_PATH (260) nao pegava UM fragmento sequer e
# cegava 7.278 arquivos reais (caminhos longos de `.claude` e do OneDrive).
_MAX_ALVO = 1024
_DESCARTAVEIS_SUFIXO = ("/dev/null", "/dev/stdout", "/dev/stderr")
_DESCARTAVEIS_NOME = ("nul", "con", "prn")


def _alvo_de_escrita_plausivel(alvo: str) -> bool:
    """O alvo pode ser um caminho de arquivo neste SO? (T-024, AC-019)"""
    alvo = alvo.strip()
    if not alvo or len(alvo) > _MAX_ALVO:
        return False
    # Proibidos pelo SO: nenhum arquivo do Windows pode ter estes caracteres,
    # entao rejeitar aqui nao cega nada por construcao.
    if any(ch in _PROIBIDOS_WIN or ord(ch) < 32 for ch in alvo):
        return False
    # `:` fora da posicao de letra de unidade (`C:`) e caminho invalido ou ADS
    # do NTFS -- em nenhum dos dois casos e o arquivo que o comando toca.
    sem_drive = alvo[2:] if len(alvo) > 1 and alvo[1] == ":" else alvo
    if ":" in sem_drive:
        return False
    if any(_SEGMENTO_VARIAVEL.fullmatch(s) for s in _SEPARADOR_PATH.split(alvo)):
        return False
    # Fim em pontuacao de codigo. `)` fica de FORA: `Backup (1)` e
    # `relatorio (copia)` sao nomes reais (5 arquivos reais terminam em `)`
    # nesta maquina, todos `css(1)` de pagina salva).
    if alvo.endswith((";", ",", "'")):
        return False
    # `/dev/null)`, `> nul;` -- descartavel com sujeira colada.
    limpo = alvo.rstrip(")};,.'\"").lower().replace("\\", "/")
    if limpo.endswith(_DESCARTAVEIS_SUFIXO) or limpo.rsplit("/", 1)[-1] in _DESCARTAVEIS_NOME:
        return False
    return True


def _recurso_escrita(alvo: str, cwd):
    """Recurso de escrita de arquivo, ou None quando nao da para saber ONDE.

    Com `cwd is None` (o comando fez `cd "$VAR"`), um caminho relativo nao pode
    virar claim: resolver contra o cwd da sessao criaria claim sobre um arquivo
    que nao existe e deixaria o arquivo real desprotegido -- foi exatamente o
    que o ensaio de 12/09 mediu. Caminho absoluto nao depende do cwd e segue
    valendo, para o cwd desconhecido nao cegar o gate inteiro.
    """
    if not _alvo_de_escrita_plausivel(alvo):
        return None  # T-024/AC-019: fragmento de comando nao vira claim
    if cwd is None:
        if not _e_absoluto(alvo):
            return None
        cwd = ""
    return _resource_from_path("file", alvo, "write", None, cwd)


_REDIRECT_ALVO = re.compile(r">{1,2}(?!&)\s*(\"[^\"]*\"|'[^']*'|[^\s|;&<>]+)")
_ALVOS_DESCARTAVEIS = ("nul", "con", "prn", "/dev/null", "/dev/stdout", "/dev/stderr")


def _faixas_entre_aspas(command: str) -> list:
    """Intervalos (inicio, fim) do texto que esta DENTRO de aspas.

    Um `>` ali e dado, nao redirecionamento. Medido no ensaio de 12/09: o
    comando `python -c "print(e['event'], '->', e['path'])"` gerava claim sobre
    um arquivo de nome `, e[` -- o `->` de dentro da string foi lido como
    redirecionamento. Claim sobre arquivo inventado e ruido que ensina a
    ignorar o aviso, que e como um gate morre.

    Abre na primeira aspa e fecha so na aspa IGUAL (aspa simples dentro de
    dupla, e vice-versa, e conteudo). Aspa sem par: vale ate o fim do comando.
    """
    faixas = []
    abertura = None
    aspa = ""
    for i, ch in enumerate(command):
        if abertura is None:
            if ch in "\"'":
                abertura, aspa = i, ch
        elif ch == aspa:
            faixas.append((abertura, i))
            abertura, aspa = None, ""
    if abertura is not None:
        faixas.append((abertura, len(command)))
    return faixas


def _detectar_redirecionamento(command: str, cwd: str) -> list:
    resources = []
    faixas = _faixas_entre_aspas(command)
    for m in _REDIRECT_ALVO.finditer(command):
        if any(ini < m.start() < fim for ini, fim in faixas):
            continue  # o `>` esta dentro de uma string: e dado, nao redirecao
        alvo = m.group(1).strip("\"'")
        if not alvo or alvo.startswith("(") or alvo.lower() in _ALVOS_DESCARTAVEIS:
            continue
        r = _recurso_escrita(alvo, cwd)
        if r is not None:
            resources.append(r)
    return resources


_SED_TRIGGER = re.compile(r"\bsed\b", re.IGNORECASE)
_SED_INPLACE = re.compile(r"(?:^|\s)-i\S*|--in-place\b", re.IGNORECASE)
_TOKEN_FINAL = re.compile(r"(\"[^\"]*\"|'[^']*'|\S+)\s*$")


def _tokens_respeitando_aspas(texto: str) -> list:
    """`split()` que nao quebra dentro de aspas (T-024, AC-020).

    `seg.split()` cru transformava
    `sed -i 's/a/b/' "C:/.../Area de Trabalho/nota.md"` em tres alvos
    (`...\\Area`, `de`, `Trabalho\\nota.md`) e nenhum do arquivo real -- gate
    CEGO, nao ruidoso, em toda caminho com espaco (que aqui e a regra:
    "Area de Trabalho", "Program Files", "OneDrive - Empresa").

    Escrito a mao em vez de `shlex`: com `posix=True` ele come as contrabarras
    de caminho do Windows; com `posix=False` ele devolve as aspas coladas no
    token e engasga com aspa sem par.

    Duas protecoes que a auditoria adversarial de 17/09 exigiu, porque sem
    elas o tokenizador PERDIA alvo que o `split()` cru acertava:

      1. `\\"` dentro de aspas duplas e escape, nao fechamento --
         `sed -i "s/\\"/X/g" a.md b.md` fechava a aspa cedo e engolia os dois
         arquivos num token so. So a contrabarra seguida da MESMA aspa que
         abriu conta como escape; `C:\\Users\\x` nao e afetado.
      2. aspa sem par (`sed -i "s/a/b/ arquivo.txt`) faz o resto do comando
         virar um token gigante e o alvo real desaparecer. Nesse caso
         DEGRADAMOS para `split()`, que era o comportamento anterior -- pior
         para caminho com espaco, mas nunca pior do que o que ja havia.

      3. aspa so ABRE citacao no INICIO de um token. Sem isto, apostrofo no
         MEIO de um nome proprio parcava com o do nome seguinte e engolia os
         dois alvos: `tee C:\\dev\\Bob's\\a.log C:\\dev\\Ana's\\b.log` devolvia
         ZERO recurso, enquanto o `.split()` antigo achava os dois (medido pela
         auditoria de 17/09; 136 arquivos desta maquina tem apostrofo no
         caminho, como `NF's Megacomm x Belenus\\34773.pdf`). A protecao 2 nao
         cobria: com numero PAR de apostrofos a degradacao nunca disparava.
         ⚠️ CORRECAO DA 4a AUDITORIA: "esta e a regra do shell de verdade" e
         FALSO, e a frase fica aqui so para nao ser reescrita de novo. No
         POSIX a aspa abre citacao em QUALQUER posicao -- medido no Git Bash,
         `a'b'c/d.md` chega ao programa como `abc/d.md`. Quem reproduzia o
         shell era o tokenizador ANTIGO; este diverge dele em 5 de 260 casos
         medidos (ex.: `tee a'b'c/d.md` passa a reivindicar `a'b'c\d.md`, um
         arquivo que ninguem toca = falso negativo). Alem disso, o caso que
         motivou a mudanca nao executa assim no bash: ele pareia os apostrofos
         de `Bob's`/`Ana's` num argumento so, igual a versao antiga.
         PENDENTE: decidir entre reverter (fidelidade ao POSIX) ou manter
         (nome de arquivo com apostrofo e comum nesta maquina e o Bash do
         harness nem sempre e a origem do comando) -- com medicao limpa do
         parsing, em sessao propria. Nao mexer aqui sem essa medicao.
    """
    texto = texto.strip()
    tokens: list = []
    atual: list = []
    aspa = ""
    i = 0
    n = len(texto)
    while i < n:
        ch = texto[i]
        if aspa:
            # `\` so escapa quando vem colado na aspa que abriu; em qualquer
            # outro caso e separador de caminho do Windows e fica literal.
            if ch == "\\" and i + 1 < n and texto[i + 1] == aspa:
                atual.append(aspa)
                i += 2
                continue
            if ch == aspa:
                aspa = ""
            else:
                atual.append(ch)
        elif ch in "\"'" and not atual:
            # `not atual` = estamos no INICIO de um token (protecao 3 acima).
            # Aspa no meio de uma palavra e caractere literal do nome.
            aspa = ch
        elif ch.isspace():
            if atual:
                tokens.append("".join(atual))
                atual = []
        else:
            atual.append(ch)
        i += 1
    if aspa:
        return texto.split()  # aspa sem par: degrada para o comportamento antigo
    if atual:
        tokens.append("".join(atual))
    return tokens


def _alvos_sed(seg: str) -> list:
    """Arquivos que um `sed -i` edita: todo token depois da expressao.

    Descarta flags (`-i`, `-e`, `--in-place`), o argumento de `-e`/`-f` e a
    propria expressao (`s/a/b/`, `1d`, `/x/d`). O que sobra sao caminhos --
    e sao TODOS alvos: `sed -i 's/a/b/' f1 f2 f3` edita os tres.
    """
    tokens = _tokens_respeitando_aspas(seg)
    alvos = []
    pular = False
    viu_expressao = False
    for tok in tokens[1:]:
        if pular:
            pular = False
            viu_expressao = True
            continue
        if tok.startswith("-"):
            if tok in ("-e", "-f", "--expression", "--file"):
                pular = True
            continue
        if not viu_expressao:
            viu_expressao = True
            continue
        limpo = tok.strip("\"'")
        if limpo:
            alvos.append(limpo)
    return alvos


def _detectar_sed_inplace(command: str, cwd: str) -> list:
    resources = []
    for seg in _segmentos_bash(command):
        if not _SED_TRIGGER.search(seg) or not _SED_INPLACE.search(seg):
            continue
        # TODOS os alvos, nao so o ultimo token (achado ALTA da 4a auditoria,
        # 12/09): claim em um arquivo e silencio nos demais e pior do que nao
        # ter gate, porque passa impressao de cobertura.
        for alvo_path in _alvos_sed(seg):
            if alvo_path.startswith("s/") or alvo_path.startswith("s|"):
                continue
            r = _recurso_escrita(alvo_path, cwd)
            if r is not None:
                resources.append(r)
    return resources


_PS_WRITE_CMDLET = re.compile(r"\b(set-content|add-content|out-file)\b", re.IGNORECASE)
# Aspas primeiro: `-Path "C:/OneDrive - Empresa/x.txt"` so vem inteiro se a
# alternativa com aspas casar antes da sem aspas (T-024, AC-020).
_PS_PATH_FLAG = re.compile(
    r"-(?:path|filepath)\s+(?:\"([^\"]+)\"|'([^']+)'|([^\"'\s]+))", re.IGNORECASE
)
_PS_POSICIONAL = re.compile(r"^\s*(?:-\S+(?:\s+\S+)?\s+)*(\"[^\"]*\"|'[^']*'|[^\s\"'-][^\s]*)")


def _detectar_escrita_powershell(command: str, cwd: str) -> list:
    resources = []
    for seg in _segmentos_bash(command):
        m = _PS_WRITE_CMDLET.search(seg)
        if not m:
            continue
        resto = seg[m.end():]
        pm = _PS_PATH_FLAG.search(resto)
        if pm:
            # tres grupos alternativos: "aspas duplas", 'simples', sem aspas
            alvo = pm.group(1) or pm.group(2) or pm.group(3)
        else:
            tm = _PS_POSICIONAL.match(resto)
            alvo = tm.group(1) if tm else None
        if not alvo:
            continue
        # `-Path a.txt,b.txt` e LISTA: sem o split o id saia com a virgula
        # dentro e nao protegia arquivo nenhum (achado ALTA, 4a auditoria).
        for parte in str(alvo).split(","):
            parte = parte.strip().strip("\"'")
            if parte:
                r = _recurso_escrita(parte, cwd)
                if r is not None:
                    resources.append(r)
    return resources


# `tee f1 f2 f3` escreve nos TRES; o padrao antigo casava so o primeiro.
_TEE_TRIGGER = re.compile(r"\btee\b((?:\s+-\w+)*(?:\s+[^|;&<>]+)?)", re.IGNORECASE)


def _detectar_tee(command: str, cwd: str) -> list:
    resources = []
    for m in _TEE_TRIGGER.finditer(command):
        # T-024/AC-020: mesmo tokenizador do `sed` -- com `.split()` cru,
        # `tee "C:/Meu Vault/Daily/2026-09-17 (copia).md"` virava quatro
        # alvos quebrados e nenhum do arquivo real (achado da auditoria).
        for tok in _tokens_respeitando_aspas(m.group(1) or ""):
            if tok.startswith("-"):
                continue
            alvo_path = tok.strip("\"'")
            if alvo_path:
                r = _recurso_escrita(alvo_path, cwd)
                if r is not None:
                    resources.append(r)
    return resources


_COPIA_TRIGGER = re.compile(
    r"\b(cp|copy|copy-item|move|mv|move-item|rename-item)\b", re.IGNORECASE
)
_COPIA_FLAG_COM_VALOR = ("-destination", "-path", "-newname", "-literalpath")


def _detectar_copia_move(command: str, cwd: str) -> list:
    """`cp a b`, `copy a b`, `Copy-Item a b`, `move a b`, `Rename-Item a b`.

    O recurso e o DESTINO (quem vai ser sobrescrito); a origem e leitura.
    Era a ultima forma conhecida de escrever em arquivo com claim de peer viva
    sem o gate perceber -- achado registrado como divida pela 3a auditoria
    (12/09) e fechado aqui.

    Conservador de proposito: so classifica quando ha pelo menos DOIS caminhos
    (origem e destino). `cp -r dir/` sozinho, ou copia para diretorio, nao vira
    claim de arquivo -- prefere-se nao avisar a avisar errado.
    """
    resources = []
    for seg in _segmentos_bash(command):
        m = _COPIA_TRIGGER.search(seg)
        if not m:
            continue
        # T-024/AC-020: tokenizador que respeita aspas, senao
        # `cp origem.md "C:/Meu Vault/Daily/2026-09-17.md"` toma como destino
        # o ULTIMO pedaco depois do espaco, e o claim sai no arquivo errado --
        # pior que nao avisar, porque avisa sobre outro arquivo.
        tokens = _tokens_respeitando_aspas(seg[m.end():])
        caminhos = []
        pular = False
        for tok in tokens:
            if pular:
                pular = False
                caminhos.append(tok.strip("\"'"))
                continue
            if tok.startswith("-"):
                if tok.lower() in _COPIA_FLAG_COM_VALOR:
                    pular = True
                continue
            limpo = tok.strip("\"'")
            if limpo:
                caminhos.append(limpo)
        if len(caminhos) < 2:
            continue
        destino = caminhos[-1]
        if destino.endswith("/") or destino.endswith("\\"):
            continue  # destino e diretorio explicito
        r = _recurso_escrita(destino, cwd)
        if r is not None:
            resources.append(r)
    return resources


# `cmd /c "..."`, `powershell -Command "..."`, `bash -c '...'` — achado ALTA da
# 5a auditoria (14/09): o comando de DENTRO do subshell escapava de tudo. Sem
# isto, `powershell -Command "Get-Date > compartilhado.txt"` escrevia num arquivo
# com claim de peer viva em silencio, enquanto o MESMO comando sem o involucro
# era detectado certo — ou seja, bastava embrulhar para furar o gate.
_SUBSHELL = re.compile(
    r"\b(?:cmd(?:\.exe)?\s+/[ck]"
    r"|(?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+)*-c(?:ommand)?"
    r"|(?:bash|sh)\s+-c)\s+"
    r"(\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)


def _comandos_internos(command: str) -> list:
    """Conteudo dos subshells (`cmd /c \"...\"`, `bash -c '...'`) do comando."""
    internos = []
    for m in _SUBSHELL.finditer(command):
        interno = m.group(2) if m.group(2) is not None else m.group(3)
        if interno and interno.strip():
            internos.append(interno)
    return internos


def _detectar_escrita_bash(command: str, cwd: str) -> list:
    achados = []
    # O miolo do subshell passa pelos MESMOS detectores. Recursao de um nivel
    # so: `bash -c "cmd /c ..."` aninhado fica de fora, limitacao consciente —
    # cada nivel a mais multiplica o risco de falso positivo em texto que apenas
    # PARECE comando.
    for interno in _comandos_internos(command):
        achados.extend(_detectar_redirecionamento(interno, cwd))
        achados.extend(_detectar_sed_inplace(interno, cwd))
        achados.extend(_detectar_escrita_powershell(interno, cwd))
        achados.extend(_detectar_tee(interno, cwd))
        achados.extend(_detectar_copia_move(interno, cwd))
    achados.extend(_detectar_redirecionamento(command, cwd))
    achados.extend(_detectar_sed_inplace(command, cwd))
    achados.extend(_detectar_escrita_powershell(command, cwd))
    achados.extend(_detectar_tee(command, cwd))
    achados.extend(_detectar_copia_move(command, cwd))
    vistos = set()
    resources = []
    for r in achados:
        if r.id in vistos:
            continue
        vistos.add(r.id)
        resources.append(r)
    return resources


def _classify_bash(tool_input: dict, cwd: str):
    command = tool_input.get("command") or ""
    if not command:
        return []
    # cwd efetivo (achado #3) calculado UMA vez e usado por git/bind/
    # migracao/escrita — todos dependem de "onde o comando roda de verdade".
    cwd_efetivo = _efetivo_cwd(command, cwd)
    # None = "nao sei em que diretorio isto roda" (`cd "$VAR"`). Para git/bind/
    # migracao vira "" -- e `_detectar_git` sem repo devolve [] em vez de
    # apontar o repo da sessao, que recusaria um commit citando o repo errado.
    # Um `git -C <path>` explicito no comando continua sendo reconhecido.
    cwd_repo = "" if cwd_efetivo is None else cwd_efetivo
    resources = []
    resources.extend(_detectar_kill(command))
    resources.extend(_detectar_git(command, cwd_repo))
    resources.extend(_detectar_bind(command, cwd_repo))
    resources.extend(_detectar_migracao(command, cwd_repo))
    resources.extend(_detectar_escrita_bash(command, cwd_efetivo))
    return resources


# ---------------------------------------------------------------------------
# Entrada publica
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Browser via MCP — a colisao que ORIGINOU o projeto e a ultima sem cobertura.
# ---------------------------------------------------------------------------

# O catalogo de colisoes que abriu o cc-coord tem o browser em primeiro lugar:
# duas sessoes disputando o mesmo perfil do Playwright custaram ao Vinicius dois
# formularios de candidatura preenchidos. O gate cobria o KILL do processo
# (AC-003), mas o claim que o kill consulta **nunca era adquirido por ninguem**:
# os matchers do PreToolUse pegam Edit/Write/NotebookEdit e Bash, e as
# ferramentas do MCP passam ao largo. Medido em 12/09 no ensaio T-013 — o deny
# do cenario 4 so funcionou porque eu criei o claim a mao.
#
# A unidade de posse e o SERVIDOR MCP (`playwright`, `playwright-b`), nao a aba
# nem a ferramenta: e o servidor que segura o perfil no disco e devolve
# "Browser is already in use" para quem chega depois. Por isso o id e
# `browser:<servidor>`, que e exatamente o que o ramo de kill ja usa.
_MCP_BROWSER = re.compile(r"^mcp__([\w.-]*(?:playwright|puppeteer|browser)[\w.-]*)__(\w+)$", re.IGNORECASE)

# `browser_close` NAO mata o processo (o MCP o mantem vivo para reaproveitar) —
# ja registrado como pegadinha. Mas ele MARCA o fim do uso declarado, que e o
# unico sinal honesto que a sessao emite; por isso vira `release`, e nao um uso
# a mais.
_FERRAMENTAS_QUE_LIBERAM = ("browser_close",)
# Ferramentas de leitura pura nao tomam posse: quem so tira screenshot ou le o
# console nao esta conduzindo a sessao de navegacao. Sem esta lista, qualquer
# inspecao criaria claim e a primeira sessao a espiar travaria as outras.
_FERRAMENTAS_SO_LEITURA = (
    "browser_console_messages",
    "browser_network_requests",
    "browser_take_screenshot",
    "browser_snapshot",
    "browser_tabs",
)


def _classify_browser_mcp(tool_name: str):
    """Ferramenta de browser via MCP -> posse do perfil daquele servidor."""
    m = _MCP_BROWSER.match(tool_name or "")
    if not m:
        return []
    servidor, ferramenta = m.group(1).lower(), m.group(2).lower()
    if not ferramenta.startswith("browser_"):
        return []
    if ferramenta in _FERRAMENTAS_SO_LEITURA:
        return []
    acao = "release" if ferramenta in _FERRAMENTAS_QUE_LIBERAM else "use"
    return [Resource(kind="browser", id=f"browser:{servidor}", action=acao)]


_DISPATCH = {
    "Edit": lambda ti, cwd, ler: _classify_edit(ti, cwd, ler),
    "Write": lambda ti, cwd, ler: _classify_write(ti, cwd),
    "NotebookEdit": lambda ti, cwd, ler: _classify_notebook_edit(ti, cwd),
    "Bash": lambda ti, cwd, ler: _classify_bash(ti, cwd),
}


def classify(
    tool_name: str,
    tool_input: dict,
    cwd: str,
    ler_arquivo: Optional[LerArquivo] = None,
):
    """(tool_name, tool_input, cwd) -> list[Resource]. Pura, sem I/O direto.

    `ler_arquivo` e a unica porta de entrada de I/O, e so e usada (se
    fornecida) para derivar a faixa de linha de um `Edit`. Ferramenta
    desconhecida, ou `tool_input` sem a chave esperada, devolve lista vazia
    — nunca levanta excecao por payload inesperado.
    """
    tool_input = tool_input or {}
    handler = _DISPATCH.get(tool_name)
    if handler is None:
        return _classify_browser_mcp(tool_name)
    return handler(tool_input, cwd, ler_arquivo)
