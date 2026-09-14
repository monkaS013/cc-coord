"""ccoord.install - instalador dos hooks do cc-coord (T-12).

Ler antes de mexer:
  - .specs/coordenacao-multissessao/tasks.md T-12 (regras que nao podem ser
    erradas: um erro aqui derruba TODAS as sessoes da maquina, nao so uma).
  - .specs/coordenacao-multissessao/design.md secoes 2, 3.7.

O que este modulo faz (`instalar()`):
  (a) backup datado de `<destino_config>/settings.json`, ANTES de qualquer
      escrita real;
  (b) merge dos 7 hooks do cc-coord na chave `hooks` do `settings.json` --
      ACRESCENTA entradas as listas de cada evento, nunca substitui a lista
      nem remove entrada alheia (hooks ja em uso: `verify_gate.py`,
      `context_alert.py`, `memory_recall_start.py`, `obsidian_stop.py`,
      `pre_push_migration_gate.py`, `block_env_edit.py` -- todos continuam
      rodando depois);
  (c) copia os 7 entrypoints de `<src_repo>/hooks/` para `<destino_config>/hooks/`;
  (d) copia `<src_repo>/rules/coordenacao-sessoes.md` para
      `<destino_config>/rules/`;
  (e) registra/atualiza `env.CCOORD_SRC` em `settings.json` apontando para
      `<src_repo>/src` -- e o que permite aos entrypoints copiados (fora do
      repo, sem `src/` irmao) resolver `import ccoord` sem reimplementar o
      pacote inteiro dentro de `~/.claude/hooks/` (ver `_bootstrap_src_path()`
      em qualquer arquivo de `hooks/`, regra 8 do design);
  (f) devolve um `Relatorio` com o que mudou -- e o que `--dry-run` imprime,
      SEM tocar em nada;
  (g) reescreve `settings.json` no MESMO estilo do original (indent, CRLF/LF,
      newline final -- `_detectar_formatacao`), nao sempre indent=2/LF;
  (h) mantem no maximo `MAX_BACKUPS` backups datados, apagando os mais
      antigos (`_rotacionar_backups`).

`desinstalar()` reverte (b)/(c)/(d)/(e), removendo so o que o cc-coord pos --
nunca um hook ou entrada alheia.

`restaurar()` copia um backup datado de volta por cima de `settings.json`
(o mais recente, se nao especificado) -- unico jeito ate agora era copiar o
`.bak-*` a mao.

Regras de seguranca que este arquivo cumpre literalmente (prompt da T-12):
  1. Merge aditivo: nunca `settings["hooks"][evento] = [...]` por cima do que
     ja existe.
  2. Validar antes de gravar: serializa o resultado, roda `json.loads` no
     texto serializado, e SO ENTAO grava. Se a validacao falhar, aborta sem
     tocar no arquivo (`_validar_serializado`).
  3. Backup ANTES de qualquer escrita, com data no nome (`_nome_backup`); se
     o backup falhar, aborta sem escrever `settings.json`.
  4. PROIBIDO tmp+rename (Windows da `EPERM` com handle aberto no destino) --
     todo write aqui e direto no arquivo final (`_escrever_texto`), depois de
     backup e validacao.
  5. Idempotencia: instalar duas vezes nao duplica hook -- deteccao pelo
     nome do arquivo de entrypoint dentro do comando (`_entrada_e_nossa`).
  6. `desinstalar` so remove entradas cujo comando aponta para um dos 7
     scripts do cc-coord; qualquer outra entrada no mesmo evento/matcher
     permanece.
  7. Python 3.14 stdlib apenas.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Relatorio", "instalar", "desinstalar", "restaurar", "main"]


# ---------------------------------------------------------------------------
# Os 7 hooks e seus eventos (design.md 3.7 / tasks.md T-12)
# ---------------------------------------------------------------------------

ENV_KEY = "CCOORD_SRC"
RULE_FILENAME = "coordenacao-sessoes.md"
# Achado MEDIA da auditoria adversarial (11/09): sem limite, cada instalar()/
# desinstalar() cria um settings.json.bak-* que nunca e removido (5 ciclos =
# 10 arquivos, sem fim a vista). `_rotacionar_backups` mantem so os mais
# recentes.
MAX_BACKUPS = 10

HOOKS_SPECS: tuple[dict, ...] = (
    {
        "event": "SessionStart",
        "matcher": "",
        "script": "coord_session_start.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: mapa de peers/claims",
    },
    {
        "event": "PreToolUse",
        "matcher": "Edit|Write|NotebookEdit",
        "script": "coord_pre_write.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: checando conflito de edicao",
    },
    {
        "event": "PreToolUse",
        "matcher": "Bash",
        "script": "coord_pre_bash.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: checando conflito de comando",
    },
    {
        # A colisao que ORIGINOU o projeto (dois formularios de candidatura
        # perdidos) e a ultima a ganhar cobertura: sem este matcher, nenhuma
        # sessao adquire o claim do perfil do browser, e o ramo de kill fica
        # consultando um dono que nunca existe. Medido no ensaio T-013.
        # O matcher e regex sobre o nome da ferramenta MCP -- pega `playwright`
        # e `playwright-b`, os dois servidores configurados nesta maquina.
        "event": "PreToolUse",
        "matcher": "mcp__.*(playwright|puppeteer|browser).*__browser_.*",
        "script": "coord_pre_browser.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: posse do perfil do browser",
    },
    {
        "event": "PostToolBatch",
        "matcher": "",
        "script": "coord_post_batch.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: avisos agregados",
    },
    {
        "event": "FileChanged",
        "matcher": "",
        "script": "coord_file_changed.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: sensor de alteracao externa",
    },
    {
        "event": "Stop",
        "matcher": "",
        "script": "coord_stop.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: liberando claims de turno",
    },
    {
        "event": "SessionEnd",
        "matcher": "",
        "script": "coord_session_end.py",
        "timeout": 10,
        "status_message": "Coordenacao entre sessoes: liberando claims da sessao",
    },
)


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------


@dataclass
class Relatorio:
    comando: str  # "install" | "uninstall"
    destino_config: str
    dry_run: bool = False
    ok: bool = True
    erro: str | None = None
    settings_existia: bool = False
    backup_path: str | None = None
    hooks_adicionados: list[str] = field(default_factory=list)
    hooks_ja_presentes: list[str] = field(default_factory=list)
    # Hooks de TERCEIROS que ficam intactos, por evento. Existe para o dono poder
    # conferir, ANTES de autorizar, que a instalacao nao desliga nada dele: o
    # settings.json global tinha 13 hooks em 6 eventos em 11/09 (verify_gate,
    # obsidian_stop, block_env_edit, context_alert, memory_recall_start,
    # monday_ritual_start, pre_push_migration_gate, impeccable e 3 inline de
    # PowerShell). Relatorio que so mostra o que ENTRA nao responde a pergunta
    # que importa, que e "o que eu tenho hoje sobrevive?".
    hooks_preservados: list[str] = field(default_factory=list)
    hooks_removidos: list[str] = field(default_factory=list)
    entrypoints_copiados: list[str] = field(default_factory=list)
    entrypoints_removidos: list[str] = field(default_factory=list)
    regra_copiada: str | None = None
    regra_removida: str | None = None
    env_var: dict | None = None  # {"chave": ENV_KEY, "valor": "..."}
    mensagens: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        if self.comando == "restore":
            linhas = ["=== ccoord restore ==="]
            linhas.append(f"Destino: {self.destino_config}")
            if not self.ok:
                linhas.append(f"ABORTADO: {self.erro}")
                linhas.append("Nada foi alterado em disco.")
            else:
                linhas.append("settings.json restaurado a partir do backup.")
            if self.mensagens:
                linhas.append("")
                linhas.extend(self.mensagens)
            return "\n".join(linhas)

        titulo = "install" if self.comando == "install" else "uninstall"
        modo = " (DRY-RUN -- nada foi escrito)" if self.dry_run else ""
        linhas = [f"=== ccoord {titulo}{modo} ==="]
        linhas.append(f"Destino: {self.destino_config}")

        if not self.ok:
            linhas.append(f"ABORTADO: {self.erro}")
            linhas.append("Nada foi alterado em disco.")
            return "\n".join(linhas)

        linhas.append(
            "settings.json: "
            + ("ja existia (sera lido e mesclado)" if self.settings_existia else "nao existia (sera criado)")
        )

        if self.backup_path:
            verbo = "previsto em" if self.dry_run else "criado em"
            linhas.append(f"Backup {verbo}: {self.backup_path}")
        else:
            linhas.append("Backup: nao necessario (settings.json nao existia)")

        linhas.append("")
        if self.comando == "install":
            linhas.append("Hooks que SERAO ADICIONADOS:" if self.dry_run else "Hooks ADICIONADOS:")
            if self.hooks_adicionados:
                linhas.extend(f"  - {h}" for h in self.hooks_adicionados)
            else:
                linhas.append("  (nenhum -- os 7 ja estavam instalados)")

            linhas.append("")
            if self.hooks_preservados:
                linhas.append(
                    f"Hooks de TERCEIROS que continuam intactos ({len(self.hooks_preservados)}):"
                )
                linhas.extend(f"  - {h}" for h in self.hooks_preservados)
                linhas.append("")
                linhas.append(
                    "ATENCAO ao custo somado: num evento com mais de um hook, o "
                    "harness paga TODOS. Medido em 11/09 no PreToolUse de Edit: "
                    "block_env_edit ~43 ms + coord_pre_write ~78 ms = ~121 ms por "
                    "edicao. O limite de 150 ms do RNF-04 e por hook; o que o "
                    "usuario sente e a soma."
                )
                linhas.append("")
            linhas.append("Hooks JA PRESENTES (nao duplicados):")
            if self.hooks_ja_presentes:
                linhas.extend(f"  - {h}" for h in self.hooks_ja_presentes)
            else:
                linhas.append("  (nenhum)")

            linhas.append("")
            destino_hooks = os.path.join(self.destino_config, "hooks")
            verbo_cp = "serao copiados para" if self.dry_run else "copiados para"
            linhas.append(f"Entrypoints que {verbo_cp} {destino_hooks}:")
            for nome in self.entrypoints_copiados:
                linhas.append(f"  - {nome}")

            linhas.append("")
            if self.regra_copiada:
                verbo_r = "sera copiada" if self.dry_run else "copiada"
                linhas.append(f"Regra que {verbo_r}: rules/{RULE_FILENAME} -> {self.regra_copiada}")

            if self.env_var:
                verbo_e = "sera definida" if self.dry_run else "definida"
                linhas.append(
                    f"Variavel de ambiente {verbo_e}: {self.env_var['chave']} = {self.env_var['valor']}"
                )
        else:
            linhas.append("Hooks REMOVIDOS:" if not self.dry_run else "Hooks que SERAO REMOVIDOS:")
            if self.hooks_removidos:
                linhas.extend(f"  - {h}" for h in self.hooks_removidos)
            else:
                linhas.append("  (nenhum -- nao havia hook do cc-coord instalado)")

            linhas.append("")
            if self.entrypoints_removidos:
                linhas.append("Entrypoints removidos:")
                linhas.extend(f"  - {n}" for n in self.entrypoints_removidos)
            if self.regra_removida:
                linhas.append(f"Regra removida: {self.regra_removida}")

        if self.mensagens:
            linhas.append("")
            linhas.extend(self.mensagens)

        if self.dry_run:
            linhas.append("")
            linhas.append("Nenhuma escrita foi feita (dry-run).")

        return "\n".join(linhas)


def _erro(comando: str, destino_config: str, dry_run: bool, motivo: str) -> Relatorio:
    return Relatorio(comando=comando, destino_config=str(destino_config), dry_run=dry_run, ok=False, erro=motivo)


# ---------------------------------------------------------------------------
# IO de baixo nivel -- regra 4: nunca tmp+rename
# ---------------------------------------------------------------------------


def _settings_path(destino_config: str) -> str:
    return os.path.join(destino_config, "settings.json")


def _fwd(caminho: str) -> str:
    return str(caminho).replace("\\", "/")


_FORMATACAO_PADRAO = {"eol": "\n", "trailing_newline": False, "indent": 2}


def _detectar_formatacao(texto: str) -> dict:
    """Deriva indent/quebra-de-linha/newline-final do settings.json ORIGINAL,
    para reescrever no MESMO estilo em vez de reformatar o arquivo inteiro.

    Achado MEDIA da auditoria (11/09): `_validar_serializado`/`_escrever_texto`
    sempre usavam indent=2 e LF sem newline final, entao qualquer settings.json
    real (indent=4, CRLF do Windows, newline final -- o que a maioria dos
    editores grava) saia de um roundtrip instalar()+desinstalar() com o
    CONTEUDO logico igual mas o arquivo inteiro reescrito. Isso esconde
    qualquer diff real dentro do ruido de quem versiona/compara o arquivo.
    So detecta indent em espacos (nao tabs); tabs caem no default de 2.
    """
    eol = "\r\n" if "\r\n" in texto else "\n"
    trailing_newline = texto.endswith("\n")
    indent = _FORMATACAO_PADRAO["indent"]
    for linha in texto.splitlines()[1:]:
        despojada = linha.lstrip(" ")
        n = len(linha) - len(despojada)
        if n > 0:
            indent = n
            break
    return {"eol": eol, "trailing_newline": trailing_newline, "indent": indent}


def _ler_settings(destino_config: str) -> tuple[dict | None, bool, str | None, dict]:
    """Devolve (dados, existia, erro, formatacao). `dados` e None se o JSON for
    invalido -- nesse caso o chamador aborta sem tocar em nada. `formatacao` e
    o estilo detectado do arquivo original (`_detectar_formatacao`), ou
    `_FORMATACAO_PADRAO` quando o arquivo nao existia -- usado por quem chama
    para reescrever no mesmo estilo (ver `_detectar_formatacao`)."""
    caminho = _settings_path(destino_config)
    if not os.path.isfile(caminho):
        return {}, False, None, dict(_FORMATACAO_PADRAO)
    try:
        # newline="" preserva o CRLF/LF cru do arquivo em `texto` -- em modo
        # universal-newlines (default) o \r\n vira \n na leitura e a deteccao
        # de formatacao abaixo nunca veria CRLF.
        with open(caminho, "r", encoding="utf-8", newline="") as fh:
            texto = fh.read()
    except OSError as exc:
        return None, True, f"nao foi possivel ler {caminho}: {exc}", dict(_FORMATACAO_PADRAO)
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        return None, True, f"{caminho} contem JSON invalido ({exc}) -- abortando sem tocar no arquivo", dict(_FORMATACAO_PADRAO)
    if not isinstance(dados, dict):
        return None, True, f"{caminho} nao e um objeto JSON no topo -- abortando sem tocar no arquivo", dict(_FORMATACAO_PADRAO)
    return dados, True, None, _detectar_formatacao(texto)


def _validar_serializado(dados: dict, indent: int = 2) -> str:
    """Regra 2: serializa e roda `json.loads` no texto serializado antes de
    devolver. Levanta `ValueError` se a validacao falhar (nunca deveria,
    dado que so serializamos tipos json-nativos, mas e a garantia pedida).
    `indent` vem da formatacao detectada do arquivo original (default 2 para
    arquivo novo -- ver `_detectar_formatacao`)."""
    texto = json.dumps(dados, indent=indent, ensure_ascii=False)
    try:
        json.loads(texto)
    except json.JSONDecodeError as exc:  # pragma: no cover - defesa em profundidade
        raise ValueError(f"resultado do merge nao e JSON valido: {exc}") from exc
    return texto


def _nome_backup(agora: datetime.datetime) -> str:
    return f"settings.json.bak-{agora.strftime('%Y%m%d-%H%M%S')}"


def _caminho_backup_disponivel(destino_config: str, agora: datetime.datetime) -> str:
    """Nome de backup datado (granularidade de segundo); se ja existir um
    backup com o mesmo nome (duas instalacoes no mesmo segundo -- comum em
    teste de idempotencia), acrescenta um contador ao inves de colidir."""
    base = _nome_backup(agora)
    candidato = os.path.join(destino_config, base)
    contador = 2
    while os.path.exists(candidato):
        candidato = os.path.join(destino_config, f"{base}-{contador}")
        contador += 1
    return candidato


def _fazer_backup(destino_config: str, agora: datetime.datetime) -> str:
    """Copia o `settings.json` atual para um arquivo datado, ANTES de
    qualquer escrita (regra 3). Levanta OSError se falhar -- o chamador
    aborta sem escrever."""
    origem = _settings_path(destino_config)
    destino = _caminho_backup_disponivel(destino_config, agora)
    with open(origem, "rb") as fh:
        conteudo = fh.read()
    # escrita direta no arquivo final de backup -- nao e o settings.json em
    # si (nada le o backup concorrentemente), mas mantemos a mesma postura
    # de "sem tmp+rename" em todo o modulo por consistencia.
    with open(destino, "xb") as fh:
        fh.write(conteudo)
    return destino


def _rotacionar_backups(destino_config: str, manter: int = MAX_BACKUPS) -> list[str]:
    """Mantem no maximo `manter` backups mais recentes de settings.json,
    apagando os mais antigos.

    Achado MEDIA da auditoria (11/09): sem isso, cada instalar()/desinstalar()
    cria um `settings.json.bak-*` que nunca e removido -- 5 ciclos ja produzem
    10 arquivos, sem rotacao nem funcao de restore em todo o modulo (ver
    `restaurar()`). O nome do backup embute o timestamp (`_nome_backup`),
    entao ordenacao lexicografica == ordenacao cronologica. Best-effort: falha
    ao apagar um arquivo velho (ex. lock externo) nao aborta a operacao
    principal que chamou esta funcao.
    """
    try:
        candidatos = sorted(
            f
            for f in os.listdir(destino_config)
            if f.startswith("settings.json.bak-") and os.path.isfile(os.path.join(destino_config, f))
        )
    except OSError:
        return []
    excesso = candidatos[:-manter] if manter > 0 and len(candidatos) > manter else []
    removidos: list[str] = []
    for nome in excesso:
        try:
            os.remove(os.path.join(destino_config, nome))
            removidos.append(nome)
        except OSError:
            pass
    return removidos


def _escrever_texto(caminho: str, texto: str, *, eol: str = "\n", trailing_newline: bool = False) -> None:
    """Regra 4: grava DIRETO no arquivo final, nunca via tmp+rename.

    `eol`/`trailing_newline` vem da formatacao detectada do settings.json
    ORIGINAL (`_detectar_formatacao`) -- default (LF, sem newline final) so
    se aplica a arquivo novo. `texto` (de `_validar_serializado`) sempre usa
    "\\n" internamente; convertemos aqui e escrevemos com newline="" para nao
    deixar o modo texto do Windows re-traduzir por cima."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    if eol != "\n":
        texto = texto.replace("\n", eol)
    if trailing_newline and not texto.endswith(eol):
        texto += eol
    with open(caminho, "w", encoding="utf-8", newline="") as fh:
        fh.write(texto)


def _copiar_arquivo(origem: str, destino: str) -> None:
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(origem, "rb") as fh:
        conteudo = fh.read()
    with open(destino, "wb") as fh:
        fh.write(conteudo)


# ---------------------------------------------------------------------------
# Merge / remocao de hooks -- regra 1 (aditivo) e regra 5 (idempotencia)
# ---------------------------------------------------------------------------


def _hooks_de_terceiros(settings: dict | None) -> list[str]:
    """Lista os hooks que NAO sao do cc-coord, por evento, para o relatorio.

    Serve para o dono conferir antes de autorizar que a instalacao e aditiva.
    Leitura puramente defensiva: settings mal formado nao pode derrubar o
    relatorio -- um dry-run que estoura e pior do que um dry-run incompleto.
    """
    if not isinstance(settings, dict):
        return []
    nossos = {spec["script"] for spec in HOOKS_SPECS}
    achados: list[str] = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    for evento, grupos in sorted(hooks.items()):
        if not isinstance(grupos, list):
            continue
        for grupo in grupos:
            if not isinstance(grupo, dict):
                continue
            matcher = grupo.get("matcher") or ""
            for entrada in grupo.get("hooks") or []:
                if not isinstance(entrada, dict):
                    continue
                comando = str(entrada.get("command") or entrada.get("type") or "?")
                if any(script in comando for script in nossos):
                    continue
                nome = comando
                for pedaco in ("/", "\\"):
                    if pedaco in nome:
                        nome = nome.rsplit(pedaco, 1)[-1]
                nome = nome.strip('"').strip()[:48] or "hook inline"
                sufixo = f" (matcher={matcher})" if matcher else ""
                achados.append(f"{evento}{sufixo} -> {nome}")
    return achados


def _entrada_e_nossa(entrada: dict, script: str) -> bool:
    comando = entrada.get("command", "")
    return isinstance(comando, str) and script in comando


def _hook_command(hooks_dir: str, script: str) -> str:
    # Achado ALTA da auditoria (11/09): gravar soh "python" confia no PATH do
    # shell resolver no momento em que o HARNESS invoca o hook via bash -c --
    # nao no momento deste instalar(). Trocar/reinstalar o Python, uma
    # distribuicao (Anaconda) empurrando seu proprio "python" na frente do
    # PATH, ou o stub da Microsoft Store quebram os 7 hooks ao mesmo tempo, e
    # o modo de falha e pior que um erro comum: falha no BASH, antes do
    # try/except de seguranca do proprio entrypoint rodar, entao um kill de
    # peer viva sai liberado mesmo assim (so que com erro visivel poluindo
    # cada tool call). Gravamos o interpretador ABSOLUTO usado para rodar este
    # instalador (`sys.executable`), que independe do PATH la na frente.
    caminho = _fwd(os.path.join(hooks_dir, script))
    interpretador = _fwd(sys.executable) if sys.executable else "python"
    return f'"{interpretador}" "{caminho}"'


def _descricao(spec: dict) -> str:
    matcher = spec["matcher"] or "(vazio)"
    return f'{spec["event"]} (matcher={matcher}) -> {spec["script"]}'


class SettingsMalFormado(ValueError):
    """`settings.json` existe mas nao tem a forma que o merge assume."""


def _merge_hooks(settings: dict, hooks_dir: str) -> tuple[dict, list[str], list[str]]:
    novo = copy.deepcopy(settings)
    # Achado por auditoria adversarial (11/09): com `hooks` como LISTA em vez de
    # dict, o `setdefault` abaixo estourava AttributeError -- inclusive em
    # --dry-run, que tem de ser sempre seguro. Nao havia perda de dado (o crash
    # vem antes de qualquer escrita), mas quebrava o contrato de devolver
    # Relatorio(ok=False) com motivo legivel. Abortar aqui e o caminho certo:
    # settings com forma inesperada e exatamente quando NAO se deve mexer.
    bruto = novo.get("hooks")
    if bruto is None:
        # cobre tanto a chave AUSENTE quanto `"hooks": null` explicito -- o
        # `setdefault` sozinho nao resolvia o segundo caso (a chave existe, o
        # valor e None) e estourava AttributeError adiante. Achado pela 2a
        # auditoria adversarial em 11/09.
        hooks_root = {}
        novo["hooks"] = hooks_root
    elif isinstance(bruto, dict):
        hooks_root = bruto
    else:
        raise SettingsMalFormado(
            f'settings.json tem "hooks" como {type(bruto).__name__}, e o esperado '
            "e um objeto {evento: [grupos]} -- abortando sem tocar em nada. "
            "Confira o arquivo a mao antes de instalar."
        )

    # Validar a forma ANINHADA, nao so o topo: a 2a auditoria achou 5 variantes
    # (evento como string/dict, grupo como lista/string, grupo["hooks"] como
    # dict) que passavam pela checagem de topo e estouravam AttributeError
    # cru la dentro -- inclusive em --dry-run, que tem de ser sempre seguro.
    # Um settings com forma inesperada e exatamente quando NAO se deve mexer.
    for evento, grupos in hooks_root.items():
        if not isinstance(grupos, list):
            raise SettingsMalFormado(
                f'settings.json tem hooks["{evento}"] como {type(grupos).__name__}, '
                "e o esperado e uma lista de grupos -- abortando sem tocar em nada."
            )
        for i, grupo in enumerate(grupos):
            if not isinstance(grupo, dict):
                raise SettingsMalFormado(
                    f'settings.json tem hooks["{evento}"][{i}] como '
                    f"{type(grupo).__name__}, e o esperado e um objeto "
                    "-- abortando sem tocar em nada."
                )
            entradas = grupo.get("hooks")
            if entradas is not None and not isinstance(entradas, list):
                raise SettingsMalFormado(
                    f'settings.json tem hooks["{evento}"][{i}]["hooks"] como '
                    f"{type(entradas).__name__}, e o esperado e uma lista "
                    "-- abortando sem tocar em nada."
                )
    adicionados: list[str] = []
    presentes: list[str] = []

    for spec in HOOKS_SPECS:
        grupos = hooks_root.setdefault(spec["event"], [])
        grupo_alvo = None
        for grupo in grupos:
            if grupo.get("matcher", "") == spec["matcher"]:
                grupo_alvo = grupo
                break
        if grupo_alvo is None:
            grupo_alvo = {"matcher": spec["matcher"], "hooks": []}
            grupos.append(grupo_alvo)

        lista_hooks = grupo_alvo.setdefault("hooks", [])
        if any(_entrada_e_nossa(h, spec["script"]) for h in lista_hooks):
            presentes.append(_descricao(spec))
            continue

        entrada = {
            "type": "command",
            "command": _hook_command(hooks_dir, spec["script"]),
            "shell": "bash",
            "timeout": spec["timeout"],
        }
        if spec.get("status_message"):
            entrada["statusMessage"] = spec["status_message"]
        lista_hooks.append(entrada)
        adicionados.append(_descricao(spec))

    return novo, adicionados, presentes


def _remove_hooks(settings: dict) -> tuple[dict, list[str]]:
    novo = copy.deepcopy(settings)
    hooks_root = novo.get("hooks")
    if not isinstance(hooks_root, dict):
        return novo, []

    removidos: list[str] = []
    for spec in HOOKS_SPECS:
        grupos = hooks_root.get(spec["event"])
        if not isinstance(grupos, list):
            continue
        for grupo in list(grupos):
            if grupo.get("matcher", "") != spec["matcher"]:
                continue
            lista_hooks = grupo.get("hooks", [])
            restantes = [h for h in lista_hooks if not _entrada_e_nossa(h, spec["script"])]
            if len(restantes) != len(lista_hooks):
                removidos.append(_descricao(spec))
            if restantes:
                grupo["hooks"] = restantes
            else:
                grupos.remove(grupo)
        if not grupos:
            del hooks_root[spec["event"]]

    return novo, removidos


def _merge_env(settings: dict, ccoord_src: str) -> tuple[dict, str]:
    novo = copy.deepcopy(settings)
    env = novo.setdefault("env", {})
    valor = _fwd(os.path.join(ccoord_src, "src"))
    env[ENV_KEY] = valor
    return novo, valor


def _remove_env(settings: dict) -> dict:
    novo = copy.deepcopy(settings)
    env = novo.get("env")
    if isinstance(env, dict):
        env.pop(ENV_KEY, None)
    return novo


# ---------------------------------------------------------------------------
# instalar()
# ---------------------------------------------------------------------------


def instalar(
    destino_config: str | Path,
    src_repo: str | Path,
    dry_run: bool = False,
    *,
    _agora: datetime.datetime | None = None,
) -> Relatorio:
    """Instala os 7 hooks do cc-coord em `destino_config` (normalmente
    `~/.claude`, MAS o chamador de producao real e o unico autorizado a usar
    o default -- testes SEMPRE passam um diretorio tempfile).

    `src_repo` e a raiz do repositorio cc-coord (onde moram `hooks/`,
    `rules/` e `src/`).

    `_agora` e um parametro interno (keyword-only) para tornar o nome do
    backup deterministico em teste; nunca precisa ser passado em uso normal.
    """
    destino_config = str(destino_config)
    src_repo = str(src_repo)
    agora = _agora or datetime.datetime.now()

    # ---- validacao ANTES de qualquer escrita -----------------------------
    hooks_src_dir = os.path.join(src_repo, "hooks")
    for spec in HOOKS_SPECS:
        origem = os.path.join(hooks_src_dir, spec["script"])
        if not os.path.isfile(origem):
            return _erro(
                "install", destino_config, dry_run,
                f"entrypoint ausente no repo: {origem} -- abortando sem tocar em nada",
            )

    regra_origem = os.path.join(src_repo, "rules", RULE_FILENAME)
    if not os.path.isfile(regra_origem):
        return _erro(
            "install", destino_config, dry_run,
            f"regra ausente no repo: {regra_origem} -- abortando sem tocar em nada",
        )

    settings, existia, erro_leitura, formatacao = _ler_settings(destino_config)
    if erro_leitura is not None:
        return _erro("install", destino_config, dry_run, erro_leitura)

    hooks_dest_dir = os.path.join(destino_config, "hooks")
    try:
        novo_settings, adicionados, presentes = _merge_hooks(settings, hooks_dest_dir)
    except SettingsMalFormado as exc:
        return _erro("install", destino_config, dry_run, str(exc))
    novo_settings, valor_env = _merge_env(novo_settings, src_repo)

    try:
        texto_final = _validar_serializado(novo_settings, indent=formatacao["indent"])
    except ValueError as exc:
        return _erro("install", destino_config, dry_run, str(exc))

    relatorio = Relatorio(
        comando="install",
        destino_config=destino_config,
        dry_run=dry_run,
        ok=True,
        settings_existia=existia,
        hooks_adicionados=adicionados,
        hooks_ja_presentes=presentes,
        hooks_preservados=_hooks_de_terceiros(settings),
        entrypoints_copiados=[spec["script"] for spec in HOOKS_SPECS],
        regra_copiada=os.path.join(destino_config, "rules", RULE_FILENAME),
        env_var={"chave": ENV_KEY, "valor": valor_env},
    )

    if existia:
        relatorio.backup_path = os.path.join(destino_config, _nome_backup(agora))
    else:
        relatorio.mensagens.append(
            "settings.json ainda nao existe neste destino; sera criado do zero."
        )

    if dry_run:
        return relatorio

    # ---- escrita de verdade, na ordem exigida -----------------------------
    os.makedirs(destino_config, exist_ok=True)

    if existia:
        try:
            caminho_backup = _fazer_backup(destino_config, agora)
        except OSError as exc:
            return _erro(
                "install", destino_config, dry_run,
                f"backup de settings.json falhou ({exc}) -- abortando sem escrever nada",
            )
        relatorio.backup_path = caminho_backup
        _rotacionar_backups(destino_config)

    # ORDEM IMPORTA, e antes estava invertida (achado ALTA da 3a auditoria,
    # 11/09): o settings.json era gravado ANTES de copiar os entrypoints. Se a
    # copia falhasse no meio -- disco cheio, permissao, `hooks` sendo um arquivo
    # em vez de diretorio -- o settings ficava apontando para scripts que NAO
    # existem e a excecao subia crua, sem Relatorio e sem aviso. O resultado e o
    # pior possivel: TODA sessao nova passa a rodar hook inexistente.
    # Agora: copiar primeiro (nada aponta para eles ainda, entao arquivo copiado
    # sem settings e inofensivo), e so entao publicar o settings, que e o gesto
    # que "liga" tudo de uma vez.
    copiados: list[str] = []
    try:
        for spec in HOOKS_SPECS:
            origem = os.path.join(hooks_src_dir, spec["script"])
            destino = os.path.join(hooks_dest_dir, spec["script"])
            _copiar_arquivo(origem, destino)
            copiados.append(destino)

        destino_regra = os.path.join(destino_config, "rules", RULE_FILENAME)
        _copiar_arquivo(regra_origem, destino_regra)
        copiados.append(destino_regra)
    except OSError as exc:
        # rollback do que ja foi copiado: sem o settings publicado, sao apenas
        # arquivos soltos, mas deixa-los seria sujeira que a proxima instalacao
        # confundiria com instalacao anterior.
        for caminho in copiados:
            try:
                os.remove(caminho)
            except OSError:
                pass
        return _erro(
            "install", destino_config, dry_run,
            f"copia dos entrypoints falhou ({exc}) -- settings.json NAO foi alterado "
            "e os arquivos ja copiados foram removidos. Nada ficou pela metade.",
        )

    try:
        _escrever_texto(
            _settings_path(destino_config), texto_final,
            eol=formatacao["eol"], trailing_newline=formatacao["trailing_newline"],
        )
    except OSError as exc:
        for caminho in copiados:
            try:
                os.remove(caminho)
            except OSError:
                pass
        return _erro(
            "install", destino_config, dry_run,
            f"escrita do settings.json falhou ({exc}) -- os entrypoints copiados foram "
            f"removidos. O backup anterior segue em {relatorio.backup_path or '(nao havia)'}.",
        )

    relatorio.regra_copiada = os.path.join(destino_config, "rules", RULE_FILENAME)
    return relatorio


# ---------------------------------------------------------------------------
# desinstalar()
# ---------------------------------------------------------------------------


def desinstalar(destino_config: str | Path, *, _agora: datetime.datetime | None = None) -> Relatorio:
    """Remove SOMENTE o que `instalar()` acrescentou: as 7 entradas de hook
    (por matcher/comando), o valor de `env.CCOORD_SRC`, os 7 entrypoints
    copiados e a regra copiada. Qualquer hook/entrada alheia no mesmo evento
    ou matcher permanece intacta."""
    destino_config = str(destino_config)
    agora = _agora or datetime.datetime.now()

    settings, existia, erro_leitura, formatacao = _ler_settings(destino_config)
    if erro_leitura is not None:
        return _erro("uninstall", destino_config, False, erro_leitura)

    relatorio = Relatorio(
        comando="uninstall",
        destino_config=destino_config,
        dry_run=False,
        ok=True,
        settings_existia=existia,
    )

    if not existia:
        relatorio.mensagens.append("settings.json nao existe neste destino; nada a remover la.")
    else:
        novo_settings, removidos = _remove_hooks(settings)
        novo_settings = _remove_env(novo_settings)
        relatorio.hooks_removidos = removidos

        try:
            texto_final = _validar_serializado(novo_settings, indent=formatacao["indent"])
        except ValueError as exc:
            return _erro("uninstall", destino_config, False, str(exc))

        try:
            caminho_backup = _fazer_backup(destino_config, agora)
        except OSError as exc:
            return _erro(
                "uninstall", destino_config, False,
                f"backup de settings.json falhou ({exc}) -- abortando sem escrever nada",
            )
        relatorio.backup_path = caminho_backup
        _rotacionar_backups(destino_config)

        # Achado ALTA da auditoria (11/09): esta escrita NAO tinha try/except --
        # settings.json marcado read-only por fora (ou disco cheio, ACL negada)
        # fazia a excecao subir crua ate o chamador (`ccoord uninstall` como
        # subprocesso recebia um traceback em vez de Relatorio(ok=False)),
        # mesmo com o backup do passo anterior ja tendo sido criado com
        # sucesso. Abortar aqui (return) tambem preserva a regra 6: se a
        # escrita falhar, NAO seguimos para a limpeza dos entrypoints/regra
        # abaixo -- settings.json continua (sem alteracao) referenciando esses
        # arquivos, entao apaga-los teria criado o MESMO estado corrompido do
        # achado 2 (settings apontando para hook que nao existe), so que pelo
        # caminho do uninstall em vez do install.
        try:
            _escrever_texto(
                _settings_path(destino_config), texto_final,
                eol=formatacao["eol"], trailing_newline=formatacao["trailing_newline"],
            )
        except OSError as exc:
            return _erro(
                "uninstall", destino_config, False,
                f"escrita do settings.json falhou ({exc}) -- o backup do estado anterior "
                f"segue em {relatorio.backup_path}. Nada foi removido do disco (entrypoints "
                "e regra continuam instalados, pois o settings.json ainda os referencia).",
            )

    # ---- limpeza best-effort dos arquivos copiados (independente de
    # settings.json existir ou nao -- podem ter sobrado de uma instalacao
    # anterior mesmo que o settings.json tenha sido apagado por fora) -------
    hooks_dest_dir = os.path.join(destino_config, "hooks")
    for spec in HOOKS_SPECS:
        caminho = os.path.join(hooks_dest_dir, spec["script"])
        if os.path.isfile(caminho):
            try:
                os.remove(caminho)
                relatorio.entrypoints_removidos.append(spec["script"])
            except OSError:
                relatorio.mensagens.append(f"nao foi possivel remover {caminho}")

    caminho_regra = os.path.join(destino_config, "rules", RULE_FILENAME)
    if os.path.isfile(caminho_regra):
        try:
            os.remove(caminho_regra)
            relatorio.regra_removida = caminho_regra
        except OSError:
            relatorio.mensagens.append(f"nao foi possivel remover {caminho_regra}")

    return relatorio


# ---------------------------------------------------------------------------
# restaurar() -- achado MEDIA da auditoria (11/09): o modulo so ESCREVIA
# backups, nunca lia um de volta ("nenhum lugar no repo... restaura de
# verdade"); quem precisasse reverter so tinha o caminho manual de copiar o
# `.bak-*` por cima do settings.json a mao.
# ---------------------------------------------------------------------------


def restaurar(
    destino_config: str | Path,
    caminho_backup: str | Path | None = None,
    *,
    _agora: datetime.datetime | None = None,
) -> Relatorio:
    """Restaura `settings.json` a partir de um backup criado por `instalar()`
    ou `desinstalar()`. Sem `caminho_backup`, usa o mais recente (o nome do
    backup embute o timestamp -- `_nome_backup` -- entao ordenacao
    lexicografica == cronologica).

    Copia os BYTES crus do backup de volta (nao reserializa), entao o
    resultado e byte-a-byte identico ao backup escolhido -- reverter tambem
    pode dar errado, e "restaurar" que na verdade reformata ou perde algo do
    original nao seria restaurar de verdade. Antes de sobrescrever, faz backup
    do settings.json ATUAL (regra 3, a mesma do resto do modulo) -- assim
    desfazer uma restauracao errada nao depende de o dono ter guardado o
    estado anterior a mao.
    """
    destino_config = str(destino_config)
    agora = _agora or datetime.datetime.now()

    if caminho_backup is None:
        candidatos: list[str] = []
        if os.path.isdir(destino_config):
            candidatos = sorted(
                f
                for f in os.listdir(destino_config)
                if f.startswith("settings.json.bak-") and os.path.isfile(os.path.join(destino_config, f))
            )
        if not candidatos:
            return _erro(
                "restore", destino_config, False,
                f"nenhum backup (settings.json.bak-*) encontrado em {destino_config}",
            )
        caminho_backup = os.path.join(destino_config, candidatos[-1])
    else:
        caminho_backup = str(caminho_backup)

    if not os.path.isfile(caminho_backup):
        return _erro("restore", destino_config, False, f"backup nao encontrado: {caminho_backup}")

    try:
        with open(caminho_backup, "rb") as fh:
            bruto_backup = fh.read()
    except OSError as exc:
        return _erro("restore", destino_config, False, f"nao foi possivel ler o backup {caminho_backup}: {exc}")

    try:
        dados_backup = json.loads(bruto_backup.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _erro(
            "restore", destino_config, False,
            f"{caminho_backup} nao contem JSON valido ({exc}) -- abortando sem tocar em settings.json",
        )
    if not isinstance(dados_backup, dict):
        return _erro(
            "restore", destino_config, False,
            f"{caminho_backup} nao e um objeto JSON no topo -- abortando sem tocar em settings.json",
        )

    relatorio = Relatorio(comando="restore", destino_config=destino_config, ok=True)
    relatorio.mensagens.append(f"Restaurado a partir de: {caminho_backup}")

    caminho_settings = _settings_path(destino_config)
    if os.path.isfile(caminho_settings):
        try:
            caminho_backup_atual = _fazer_backup(destino_config, agora)
        except OSError as exc:
            return _erro(
                "restore", destino_config, False,
                f"backup do settings.json atual falhou ({exc}) -- abortando sem restaurar",
            )
        relatorio.backup_path = caminho_backup_atual
        relatorio.mensagens.append(f"settings.json anterior (pre-restore) salvo em: {caminho_backup_atual}")
        _rotacionar_backups(destino_config)

    try:
        _copiar_arquivo(caminho_backup, caminho_settings)
    except OSError as exc:
        return _erro(
            "restore", destino_config, False,
            f"escrita do settings.json restaurado falhou ({exc}). O backup do estado anterior "
            f"segue em {relatorio.backup_path or '(nao havia settings.json antes)'}.",
        )

    return relatorio


# ---------------------------------------------------------------------------
# CLI (`ccoord install` / `ccoord uninstall` / `ccoord install --dry-run`)
# ---------------------------------------------------------------------------


def _destino_padrao() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude")


def _src_repo_padrao() -> str:
    # este arquivo mora em <repo>/src/ccoord/install.py
    aqui = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(aqui))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccoord",
        description="Instala/desinstala os hooks de coordenacao entre sessoes (cc-coord).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_install = sub.add_parser("install", help="instala os 7 hooks em ~/.claude")
    p_install.add_argument("--dry-run", action="store_true", help="mostra o que seria feito, sem escrever nada")
    p_install.add_argument("--destino", default=None, help="diretorio de config (default: ~/.claude)")
    p_install.add_argument("--src-repo", default=None, help="raiz do repo cc-coord (default: auto-detectado)")
    p_install.set_defaults(func=_cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove os hooks instalados pelo cc-coord")
    p_uninstall.add_argument("--destino", default=None, help="diretorio de config (default: ~/.claude)")
    p_uninstall.set_defaults(func=_cmd_uninstall)

    p_restore = sub.add_parser("restore", help="restaura settings.json a partir de um backup do cc-coord")
    p_restore.add_argument("--destino", default=None, help="diretorio de config (default: ~/.claude)")
    p_restore.add_argument("--backup", default=None, help="caminho do backup a restaurar (default: o mais recente)")
    p_restore.set_defaults(func=_cmd_restore)

    return parser


def _cmd_install(args: argparse.Namespace) -> Relatorio:
    destino = args.destino or _destino_padrao()
    src_repo = args.src_repo or _src_repo_padrao()
    return instalar(destino, src_repo, dry_run=args.dry_run)


def _cmd_uninstall(args: argparse.Namespace) -> Relatorio:
    destino = args.destino or _destino_padrao()
    return desinstalar(destino)


def _cmd_restore(args: argparse.Namespace) -> Relatorio:
    destino = args.destino or _destino_padrao()
    return restaurar(destino, args.backup)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    relatorio = args.func(args)
    print(relatorio.to_text())
    return 0 if relatorio.ok else 1


if __name__ == "__main__":
    sys.exit(main())
