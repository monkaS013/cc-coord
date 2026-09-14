"""ccoord.policy — a tabela de decisao (design.md secao 3.4).

Funcao PURA: `decide(resource, owner, me, peers, contexto=None) -> Decision`.
Nenhum I/O acontece aqui dentro — nem ler `~/.claude/coord/claims/`, nem
`~/.claude/sessions/*.json`, nem chamar git ou consultar processo. Tudo isso
ja foi resolvido por quem chama (os hooks finos de `hookio.py`/entrypoints,
que SAO impuros) e chega aqui como dado:

- `resource`: um `ccoord.classify.Resource` (o que a chamada de ferramenta
  toca).
- `owner`: o `ccoord.claims.Claim` atual do recurso (path/range/owner/scope),
  ou `None` quando o recurso esta livre (nunca existiu claim, ou quem chamou
  ja decidiu que o claim existente e lixo). Este modulo NAO decide "morto"
  sozinho por conta propria em cima do `owner` — quem decide liveness e
  `peers` (ver abaixo), exceto no ramo de kill (regra 2).
- `me`: a `ccoord.sessions.Session` da sessao atual (ou None).
- `peers`: lista de `ccoord.sessions.Session` VIVAS (ja filtradas por
  `sessions.peers()` do lado de quem chama). E o oraculo de liveness deste
  modulo: um `owner.owner.session_id` que aparece aqui e peer viva; um que
  nao aparece (e nao e `me`) e dono morto — EXCETO no ramo de kill.
- `contexto`: dict opcional com dados que exigiriam I/O para calcular e que
  quem chama ja calculou: `estado_ilegivel` (bool), `porta_livre` (int),
  `commits_alheios` (list[str]).

Regras que nao podem ser erradas (do prompt da task, e do design.md secao 0
e 3.4):

1. A politica e "avisar sempre, bloquear so o irreversivel". `deny` so em
   kill de processo e `git commit`/`push` com peer viva no mesmo repo.
2. O ramo de kill NUNCA decide por idade do processo. Mesmo que o `owner` do
   claim pareca "morto" (sessao coordenadora nao aparece em `peers`), o kill
   e recusado do mesmo jeito — porque coordenador morto != processo (browser/
   servidor) orfao (design.md secao 0, ultimo achado). So um `owner is None`
   (nunca existiu claim para este recurso) ou `owner` seu proprio libera o
   kill. Autorizacao de kill e do Vinicius, nunca de uma peer.
3. Toda `deny` e prescritiva: nomeia o dono e o comando/caminho alternativo.
4. Fail-open/fail-closed: `contexto["estado_ilegivel"]=True` -> `allow` para
   tudo, EXCETO kill, que vira `deny`.
5. Dono morto nunca bloqueia nada (fora do ramo de kill).
6. Sem I/O.
7. Razao truncada em 2000 caracteres / 20 linhas (teto do
   `permissionDecisionReason` do harness).

Custo de import (T-017, RNF-04): este modulo antes importava `ccoord.claims`/
`ccoord.classify`/`ccoord.sessions` SO para anotar tipos (`Claim`, `Resource`,
`Session` nunca sao instanciados nem usados via `isinstance` aqui dentro - so
acessados por duck typing, `owner.owner.session_id` etc.). Com
`from __future__ import annotations`, anotacoes viram string e nunca sao
avaliadas - os imports eram 100% custo, zero beneficio em runtime. Removidos
(junto com `dataclasses`/`typing`, mesmo motivo de `ccoord.claims`); os tipos
esperados ficam documentados nos comentarios de `decide()`. `Decision` virou
classe simples (nao `@dataclass`).
"""

from __future__ import annotations

__all__ = ["Decision", "decide"]

_MAX_CHARS = 2000
_MAX_LINHAS = 20

_ACOES_GIT_ESCRITA = ("commit", "push")


class Decision:
    """Resultado de `decide()`.

    - verdict: "allow" | "warn" | "deny"
    - reason: texto prescritivo (ja truncado ao teto do harness)
    - severity: "info" | "forte"
    - resource: `resource.id` que motivou a decisao
    - owner: nome (ou session_id) do dono que motivou, quando houver
    """

    def __init__(
        self,
        verdict: str,
        reason: str,
        severity: str,
        resource: str,
        owner: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.reason = reason
        self.severity = severity
        self.resource = resource
        self.owner = owner


# ---------------------------------------------------------------------------
# Utilitarios puros de string (sem os.path — nada de I/O, so manipulacao)
# ---------------------------------------------------------------------------


def _norm(caminho: Optional[str]) -> str:
    # Achado [ALTA] grupo policy: `caminho` vem de `Session.cwd`/`Resource.path`,
    # que por sua vez vem de ~/.claude/sessions/<pid>.json lido por quem chama
    # (fora deste modulo, sem I/O aqui) — um registro de sessao com `cwd`
    # malformado (int, lista, dict, float, bool em vez de str) faz
    # `.strip()` levantar AttributeError. Essa excecao sobe por _mesmo_repo ->
    # _peers_no_repo -> _decidir_git_escrita/_decidir_git_outros/_reponame_do_cwd
    # -> decide(), e e' engolida pelo catch-all de hookio.executar em volta do
    # decisor: o resultado observado e' `git commit`/`push` saindo ALLOW em
    # silencio mesmo com peer viva no mesmo repo — exatamente o deny
    # irreversivel que a regra 1 do modulo exige. Alem disso, como o loop de
    # `_peers_no_repo` percorre TODAS as peers numa unica chamada, um unico
    # registro malformado aborta a checagem para as demais peers (mesmo as
    # com `cwd` valido), perdendo a protecao inteira naquele instante — nao
    # so para a peer com dado ruim. Tratamos tipo errado como "sem
    # informacao" (mesmo resultado de cwd ausente/vazio: essa peer especifica
    # deixa de contar como "no mesmo repo"), sem abortar a avaliacao das
    # demais.
    if not isinstance(caminho, str) or not caminho:
        return ""
    return caminho.strip().replace("\\", "/").rstrip("/").lower()


def _mesmo_repo(peer_cwd: Optional[str], repo_path: Optional[str]) -> bool:
    """A peer esta trabalhando DENTRO deste repo?

    Conta o proprio diretorio do repo e qualquer subdiretorio dele (uma peer em
    `repo/src` esta no repo). NAO conta o caminho inverso -- peer num diretorio
    ANCESTRAL do repo --, e isso e uma correcao de 12/09 achada pelo uso real:
    a condicao antiga (`b.startswith(a + "/")`) tratava uma sessao aberta na
    HOME como "no mesmo repositorio" de qualquer repo abaixo dela. Como a home
    e ancestral de tudo, **uma unica sessao ociosa ali bloqueava todo commit e
    push da maquina** -- o gate recusou o meu proprio push do cc-coord por causa
    de duas sessoes paradas em `C:\\Users\\ViniciusMoraisHDT`.

    Atrito puro, do tipo que a politica existe para evitar: estar acima de um
    repo nao e evidencia de estar mexendo nele. O preco e um falso negativo
    estreito -- peer registrada na home que faz `cd <repo> && git commit` --,
    muito melhor que recusar trabalho legitimo o tempo todo.
    """
    a = _norm(peer_cwd)
    b = _norm(repo_path)
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/")


def _reponame_do_cwd(cwd: Optional[str]) -> str:
    norm = _norm(cwd)
    segs = [s for s in norm.split("/") if s]
    return segs[-1] if segs else ""


def _dono_nome(claim: Claim) -> str:
    return claim.owner.name or claim.owner.session_id or "peer desconhecida"


def _nome_peer_seguro(p: Session) -> str:
    """Nome de exibicao de uma peer, sempre `str` — mesma familia do achado
    [ALTA] de `cwd` (registro de sessao em disco com campo de tipo errado).

    `nomes = ", ".join(sorted({p.name or p.session_id or "peer" ...}))` (usado
    em _decidir_git_escrita/_decidir_git_outros/_decidir_migracao) quebra do
    mesmo jeito se `name`/`session_id` vier como int/lista/dict em vez de str:
    `sorted()` sobre um set de tipos mistos (str + int, por exemplo) levanta
    `TypeError` (tipos nao comparaveis), e mesmo com um unico item nao-str
    `", ".join(...)` ja levanta `TypeError` ("sequence item 0: expected str
    instance, int found") antes de chegar no sorted. E' a mesma excecao subindo
    ate decide() e sendo engolida pelo catch-all do chamador — `git
    commit`/`push` sairia ALLOW em silencio pelo mesmo motivo, so que via
    `name` em vez de `cwd`. Forcamos str aqui para que um registro malformado
    nao aborte a decisao para as demais peers.
    """
    valor = p.name or p.session_id or "peer"
    return valor if isinstance(valor, str) else str(valor)


def _trunca_razao(texto: str) -> str:
    linhas = texto.splitlines()
    if len(linhas) > _MAX_LINHAS:
        linhas = linhas[: _MAX_LINHAS - 1] + ["... (razao truncada, ver events.log)"]
        texto = "\n".join(linhas)
    if len(texto) > _MAX_CHARS:
        texto = texto[: _MAX_CHARS - 25].rstrip() + " ... (truncado)"
    return texto


def _ranges_overlap(a, b) -> bool:
    """Mesma semantica de `claims._ranges_overlap`: None colide com tudo."""
    if a is None or b is None:
        return True
    return a[0] <= b[1] and b[0] <= a[1]


def _fmt_faixa(faixa) -> str:
    if faixa is None:
        return "o arquivo inteiro"
    return f"as linhas {faixa[0]}-{faixa[1]}"


def _claim_de_peer_viva(owner: Optional[Claim], me: Optional[Session], peers: list) -> bool:
    """True quando `owner` e um claim de peer VIVA que nao sou eu.

    `peers` ja veio filtrada para so conter sessoes vivas (regra do sessions.py
    - `is_alive()`); um `session_id` que nao aparece ali e dono morto, e dono
    morto nunca bloqueia (regra 5) — EXCETO no ramo de kill, que nao usa esta
    funcao (ver `_decidir_kill`).
    """
    if owner is None:
        return False
    sid = owner.owner.session_id
    if me is not None and me.session_id is not None and sid == me.session_id:
        return False
    peer_ids = {p.session_id for p in peers if p.session_id}
    return sid in peer_ids


def _allow(resource: Resource, dono: Optional[str] = None) -> Decision:
    razao = (
        f"OK: {resource.id} sem impedimento (recurso livre, dono morto/expirado, "
        "ou o dono e voce mesmo)."
    )
    return Decision("allow", _trunca_razao(razao), "info", resource.id, dono)


# ---------------------------------------------------------------------------
# Ramo: kill de processo/browser (regra 2 — nunca decide por idade)
# ---------------------------------------------------------------------------


def _decidir_kill(
    resource: Resource,
    owner: Optional[Claim],
    me: Optional[Session],
    pedido_pelo_usuario: bool = False,
) -> Decision:
    if owner is None:
        return _allow(resource, None)

    if me is not None and me.session_id is not None and owner.owner.session_id == me.session_id:
        return _allow(resource, _dono_nome(owner))

    dono = _dono_nome(owner)

    if pedido_pelo_usuario:
        # Eixo de PROVENIENCIA (T-020, achado do ensaio T-013). O deny abaixo
        # existe para barrar kill nascido de inferencia minha. Quando o proprio
        # Vinicius nomeia o alvo no turno, a razao antiga terminava em "pergunte
        # ao Vinicius" -- pedir autorizacao a quem deu a ordem. Isso nao evita
        # perda: ele mata por fora, sem gate e sem registro. Entao vira warn com
        # o CUSTO explicito, que e a informacao que ele nao tem.
        razao = (
            f"ATENCAO: voce pediu para matar {resource.id}, e ele tem claim de "
            f"{dono} (sessao viva). Matar agora derruba o que essa sessao esta "
            "fazendo, e o trabalho dela nao volta. Segue liberado porque a ordem "
            "e sua, nao inferencia minha. Antes de confirmar, considere: mandar "
            f"SendMessage para {dono} pedindo para liberar, ou usar outro "
            "servidor/perfil. Lembre que fechar o browser pelo MCP nao mata o "
            "processo — processo vivo nao prova que alguem esta usando."
        )
        return Decision("warn", _trunca_razao(razao), "forte", resource.id, dono)

    razao = (
        f"DENY: kill recusado — {resource.id} tem claim de {dono} (sessao "
        f"{owner.owner.session_id}). O ramo de kill NUNCA decide por idade do "
        "processo, mesmo que todos os sinais apontem para processo orfao: "
        "presenca de processo nao prova uso, e ausencia de sessao desenhando "
        "nao prova abandono (o MCP mantem o processo vivo entre chamadas para "
        "reaproveitar). Autorizacao de kill e do Vinicius e NAO se pede a uma "
        "peer (seria permission laundering). Alternativa concreta: use outro "
        "servidor/perfil (ex.: `playwright-b` em vez de `playwright`), ou "
        f"mande SendMessage para {dono} pedindo para liberar e aguarde com "
        "notify_when_idle. Se ainda parecer orfao, pergunte ao Vinicius antes "
        "de matar — nao decida sozinho."
    )
    return Decision("deny", _trunca_razao(razao), "forte", resource.id, dono)


# ---------------------------------------------------------------------------
# Ramo: git commit/push (deny) e demais acoes git (warn)
# ---------------------------------------------------------------------------


def _peers_no_repo(resource: Resource, me: Optional[Session], peers: list) -> list:
    achados = []
    for p in peers:
        if me is not None and p.session_id == me.session_id:
            continue
        if _mesmo_repo(p.cwd, resource.path):
            achados.append(p)
    return achados


def _decidir_git_escrita(resource: Resource, me: Optional[Session], peers: list, contexto: dict) -> Decision:
    peers_no_repo = _peers_no_repo(resource, me, peers)
    if not peers_no_repo:
        return _allow(resource, None)

    nomes = ", ".join(sorted({_nome_peer_seguro(p) for p in peers_no_repo}))
    commits = [str(c) for c in (contexto.get("commits_alheios") or [])]

    # Commit com PATHSPEC explicito e sem interseccao com claim de peer viva:
    # aviso, nao recusa. O gate antigo tratava `git add <arquivo proprio> &&
    # git commit` igual a `git commit -am` -- punia quem fazia certo, e o unico
    # caminho que sobrava era ignorar o gate (achado do ensaio T-013).
    # Seguranca por construcao: o pathspec ignora o indice, entao nao ha janela
    # para a peer entrar entre o check e o commit.
    if resource.action == "commit" and contexto.get("commit_seletivo_seguro"):
        alvos = ", ".join(str(p) for p in (contexto.get("pathspecs") or [])) or "o caminho indicado"
        razao = (
            f"AVISO: {nomes} tem sessao viva em {resource.path}, mas este commit "
            f"e seletivo ({alvos}) e nenhum desses caminhos tem claim dela. O "
            "pathspec ignora o indice, entao o que ela tiver em staging nao vai "
            "junto. Segue liberado; confira a mensagem do commit e nao rode "
            "`git push` sem falar com o Vinicius — o push leva commits dela."
        )
        return Decision("warn", _trunca_razao(razao), "info", resource.id, nomes)

    partes = [
        f"DENY: git {resource.action} recusado em {resource.path} — {nomes} tem "
        "sessao viva neste mesmo repositorio."
    ]
    if commits:
        partes.append("Commit(s) alheio(s) no intervalo local: " + "; ".join(commits) + ".")
    partes.append(
        "Rode `git --no-optional-locks log origin/<branch>..HEAD` e "
        "`git status --short` (2 colunas) e leve as duas saidas ao Vinicius: "
        "se ele autorizar, ELE mesmo roda o comando por `!` no prompt, que e o "
        "unico destravamento real (o gate cobre a ferramenta Bash da sessao, "
        "nao o que ele digita)."
    )
    # As duas "alternativas" que este texto oferecia antes NAO funcionavam, e o
    # ensaio T-013 mediu o custo disso: a sessao barrada criou `b/trabalho-da-b`,
    # remediu o indice e tomou o SEGUNDO deny, com a razao repetindo "finalize
    # numa branch propria" -- a condicao e peer-viva-no-repo, e branch nao entra
    # na conta. "Aguarde a peer liberar" tambem nao e mecanismo: a peer chegou a
    # dizer "pode commitar" e o gate seguiu barrando, o que esta CERTO (peer nao
    # levanta gate do usuario, seria permission laundering) -- mas entao nao
    # pode ser oferecido como saida. Razao prescritiva que prescreve o
    # inexecutavel e pior que recusa seca: gasta turno e corroi a confianca que
    # faz a sessao nao contornar o gate.
    partes.append(
        f"Avisar {nomes} por SendMessage serve para ela saber, nao para "
        "liberar: peer nao levanta gate. Trocar a forma do comando para passar "
        "e contorno, mesmo que a forma nova seja melhor -- a forma melhor entra "
        "pela politica, com o Vinicius, nao dentro deste turno."
    )
    razao = " ".join(partes)
    return Decision("deny", _trunca_razao(razao), "forte", resource.id, nomes)


def _decidir_git_outros(resource: Resource, me: Optional[Session], peers: list) -> Decision:
    peers_no_repo = _peers_no_repo(resource, me, peers)
    if not peers_no_repo:
        return _allow(resource, None)
    nomes = ", ".join(sorted({_nome_peer_seguro(p) for p in peers_no_repo}))
    razao = (
        f"AVISO: `git {resource.action}` em {resource.path} enquanto {nomes} "
        f"esta com sessao viva aqui. Nao bloqueado (nao e commit/push), mas "
        f"{resource.action} pode mexer no que a peer esta usando — avise por "
        "SendMessage antes de seguir."
    )
    return Decision("warn", _trunca_razao(razao), "info", resource.id, nomes)


# ---------------------------------------------------------------------------
# Ramo: bind de porta/servidor
# ---------------------------------------------------------------------------


def _extrai_numero_porta(resource_id: str) -> Optional[int]:
    try:
        return int(resource_id.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return None


def _decidir_bind(resource: Resource, owner: Optional[Claim], me: Optional[Session], peers: list, contexto: dict) -> Decision:
    if not _claim_de_peer_viva(owner, me, peers):
        return _allow(resource, None)

    dono = _dono_nome(owner)
    porta_livre = contexto.get("porta_livre")
    if porta_livre is None:
        n = _extrai_numero_porta(resource.id)
        porta_livre = (n + 1) if n is not None else None
    sugestao = f"a porta {porta_livre}" if porta_livre is not None else "outra porta livre (confira com `netstat`)"

    razao = (
        f"AVISO: {resource.id} esta com lease de {dono}. Bind aqui colide em "
        f"runtime; use {sugestao} em vez desta."
    )
    return Decision("warn", _trunca_razao(razao), "info", resource.id, dono)


# ---------------------------------------------------------------------------
# Ramo: migracao de schema
# ---------------------------------------------------------------------------


def _decidir_migracao(resource: Resource, me: Optional[Session], peers: list) -> Decision:
    partes_id = resource.id.split(":")
    reponame = partes_id[1] if len(partes_id) >= 3 else ""
    if not reponame:
        return _allow(resource, None)

    peers_no_repo = [
        p
        for p in peers
        if (me is None or p.session_id != me.session_id) and _reponame_do_cwd(p.cwd) == reponame
    ]
    if not peers_no_repo:
        return _allow(resource, None)

    nomes = ", ".join(sorted({_nome_peer_seguro(p) for p in peers_no_repo}))
    razao = (
        f"AVISO FORTE: migracao de schema em '{reponame}' com {nomes} viva no "
        "mesmo repositorio. Adie a migracao e implemente so o que nao toca "
        f"schema ate ela liberar — avise {nomes} por SendMessage antes de migrar."
    )
    return Decision("warn", _trunca_razao(razao), "forte", resource.id, nomes)


# ---------------------------------------------------------------------------
# Ramo: Edit/Write de arquivo
# ---------------------------------------------------------------------------


def _decidir_arquivo(resource: Resource, owner: Optional[Claim], me: Optional[Session], peers: list) -> Decision:
    if not _claim_de_peer_viva(owner, me, peers):
        return _allow(resource, None)

    dono = _dono_nome(owner)
    faixa_txt = _fmt_faixa(owner.range)

    if resource.action == "write":
        razao = (
            f"AVISO FORTE: Write (reescrita do arquivo inteiro) em {resource.path} "
            f"— {dono} tem claim em {faixa_txt} ali. A reescrita apaga o que "
            f"entrou na janela dela. Prefira `Edit` cirurgico so no trecho "
            f"necessario; se precisar reescrever mesmo assim, avise {dono} por "
            "SendMessage antes."
        )
        return Decision("warn", _trunca_razao(razao), "forte", resource.id, dono)

    # Edit
    sobrepoe = _ranges_overlap(resource.lines, owner.range)
    if sobrepoe:
        # Texto no PASSADO de proposito: o `additionalContext` de um PreToolUse
        # que nao bloqueia chega ao modelo JUNTO com o resultado da ferramenta,
        # nunca antes dela (medido 12/09). "Avise antes de editar" e impossivel
        # de cumprir na primeira tentativa -- quando eu leio, ja editei. Aviso
        # informa; quem ordena e o deny.
        razao = (
            f"AVISO: a edicao em {resource.path} colide com {faixa_txt} de {dono}. "
            f"Ja foi aplicada — mande SendMessage para {dono} AGORA dizendo o que "
            "voce tocou (arquivo, faixa, se foi Edit pontual ou reescrita), para "
            "ela conferir se algo dela se perdeu."
        )
        return Decision("warn", _trunca_razao(razao), "info", resource.id, dono)

    razao = (
        # Parenteses em vez de preposicao: `faixa_txt` tanto e "as linhas 5-15"
        # quanto "o arquivo inteiro", e qualquer preposicao fixa erra num dos
        # dois ("em as linhas", "em o arquivo") -- visto na saida real do ensaio.
        f"Aviso curto: {dono} tambem esta em {resource.path} ({faixa_txt}) — "
        "sem sobreposicao com a sua edicao. So ciencia, nada a fazer."
    )
    return Decision("warn", _trunca_razao(razao), "info", resource.id, dono)


# ---------------------------------------------------------------------------
# Entrada publica
# ---------------------------------------------------------------------------


def decide(
    resource: Resource,
    owner: Optional[Claim] = None,
    me: Optional[Session] = None,
    peers: Optional[Iterable[Session]] = None,
    contexto: Optional[dict] = None,
) -> Decision:
    """`(resource, owner, me, peers, contexto=None) -> Decision`. Pura.

    Ver o docstring do modulo para o contrato de cada parametro. Nunca
    levanta excecao por combinacao inesperada: um `resource.kind`/`action`
    sem regra especifica cai no `allow` generico.
    """
    peers_list = list(peers) if peers else []
    ctx = contexto or {}

    if ctx.get("estado_ilegivel"):
        if resource.action == "kill":
            razao = (
                "DENY: estado de coordenacao ilegivel (claims/sessions nao "
                "puderam ser lidos). Fail-closed para kill: nao da para "
                "confirmar se o processo pertence a uma peer viva. Nao matar "
                "e sempre seguro; pergunte ao Vinicius antes de prosseguir."
            )
            return Decision("deny", _trunca_razao(razao), "forte", resource.id, None)
        razao = (
            "OK (fail-open): estado de coordenacao ilegivel, mas a acao nao e "
            "irreversivel — segue liberada. Erro fica registrado em "
            "events.log; nao trava o turno por estado corrompido."
        )
        return Decision("allow", _trunca_razao(razao), "info", resource.id, None)

    if resource.kind in ("process", "browser") and resource.action == "kill":
        # `estado_ilegivel` ja foi tratado acima e vence a proveniencia de
        # proposito: ali nao da para saber SE existe dono, entao liberar por
        # ordem do usuario seria decidir no escuro sobre algo irreversivel.
        return _decidir_kill(
            resource, owner, me, bool(ctx.get("kill_pedido_pelo_usuario"))
        )

    if resource.kind == "git" and resource.action in _ACOES_GIT_ESCRITA:
        return _decidir_git_escrita(resource, me, peers_list, ctx)

    if resource.kind == "git":
        return _decidir_git_outros(resource, me, peers_list)

    if resource.kind in ("port", "server") and resource.action == "bind":
        return _decidir_bind(resource, owner, me, peers_list, ctx)

    if resource.kind == "db" and resource.action == "migrate":
        return _decidir_migracao(resource, me, peers_list)

    if resource.kind == "file" and resource.action in ("edit", "write"):
        return _decidir_arquivo(resource, owner, me, peers_list)

    return _allow(resource, None)
