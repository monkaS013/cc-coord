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
      "port:3100", "git:c--dev-workday-hdt-ts"). Sempre em minusculas — ver
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
    absoluto inteiro ("git:C--dev-workday-hdt-ts"). Ver decisao no relato
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
_KILL_TRIGGER = re.compile(
    # Lista EXPLICITA de executaveis, nunca curinga: `\w*kill\b` cobriria
    # `pskill` mas tambem casaria `skill` -- e o dono tem uma pasta `skills/`
    # cheia delas. Falso positivo em kill vira recusa de comando inocente, que
    # e o atrito que esta feature existe para evitar.
    r"(\b(taskkill|tskill|pskill|pkill|kill|stop-process|spps)\b"
    r"|\bwmic\b[^|;&]*\b(terminate|delete)\b"  # wmic ... call terminate | delete
    r")",
    re.IGNORECASE,
)

_KILL_TARGET_PATTERNS = (
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


def _extrair_alvo_kill(command: str) -> Optional[str]:
    for pat in _KILL_TARGET_PATTERNS:
        m = pat.search(command)
        if m:
            return m.group(1)
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


def _detectar_kill(command: str):
    if not _KILL_TRIGGER.search(command):
        return []
    alvo = _extrair_alvo_kill(command)
    if alvo is None:
        return [Resource(kind="process", id="process:desconhecido", action="kill")]
    alvo_lower = alvo.lower()

    candidatos = _candidatos_wildcard(alvo_lower)
    if candidatos:
        return [
            Resource(kind="browser", id=f"browser:{nome}", action="kill")
            for nome in candidatos
        ]

    if any(nome in alvo_lower for nome in _BROWSER_NAMES):
        return [Resource(kind="browser", id=f"browser:{alvo_lower}", action="kill")]
    return [Resource(kind="process", id=f"process:{alvo_lower}", action="kill")]


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


def _recurso_escrita(alvo: str, cwd):
    """Recurso de escrita de arquivo, ou None quando nao da para saber ONDE.

    Com `cwd is None` (o comando fez `cd "$VAR"`), um caminho relativo nao pode
    virar claim: resolver contra o cwd da sessao criaria claim sobre um arquivo
    que nao existe e deixaria o arquivo real desprotegido -- foi exatamente o
    que o ensaio de 12/09 mediu. Caminho absoluto nao depende do cwd e segue
    valendo, para o cwd desconhecido nao cegar o gate inteiro.
    """
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


def _alvos_sed(seg: str) -> list:
    """Arquivos que um `sed -i` edita: todo token depois da expressao.

    Descarta flags (`-i`, `-e`, `--in-place`), o argumento de `-e`/`-f` e a
    propria expressao (`s/a/b/`, `1d`, `/x/d`). O que sobra sao caminhos --
    e sao TODOS alvos: `sed -i 's/a/b/' f1 f2 f3` edita os tres.
    """
    tokens = seg.strip().split()
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
_PS_PATH_FLAG = re.compile(r"-(?:path|filepath)\s+\"?([^\"\s]+)\"?", re.IGNORECASE)
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
            alvo = pm.group(1)
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
        for tok in (m.group(1) or "").split():
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
        tokens = seg[m.end():].split()
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


def _detectar_escrita_bash(command: str, cwd: str) -> list:
    achados = []
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
        return []
    return handler(tool_input, cwd, ler_arquivo)
