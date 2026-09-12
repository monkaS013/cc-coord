"""ccoord.proveniencia - de quem partiu a ordem: do Vinicius ou de mim.

Existe por um defeito medido no ensaio T-013 (12/09). O Vinicius mandou
`taskkill /F /PID 20448` com todas as letras; o gate devolveu `deny` e a razao
terminava em *"pergunte ao Vinicius antes de matar"* -- pedir autorizacao a quem
acabou de dar a ordem. Isso e atrito puro: nao evita perda nenhuma, e o desfecho
previsivel e ele matar o processo por fora do Claude, onde nao existe gate nem
registro. A peer perde o trabalho igual, so que em silencio.

O conserto NAO afrouxa o deny. Ele separa dois casos que a tabela de decisao
tratava como um so:

- kill nascido de INFERENCIA minha ("esse processo parece orfao") -> `deny`,
  exatamente como antes. E a unica garantia irreversivel da feature;
- kill que o usuario NOMEOU no turno -> `warn`, com o custo explicito ("a peer X
  perde o que estava fazendo").

**O que autoriza e o alvo nomeado, nao a vontade de matar.** "mata esse bug" nao
libera `process:20448`; exige-se VERBO de kill + ALVO que casa com o recurso. E
o alvo tem de vir de uma fala real do usuario -- `tool_result` chega no
transcript com `role="user"` e NAO conta, senao a saida de um comando que por
acaso contenha "taskkill /PID 20448" viraria autorizacao (eu autorizando a mim
mesma pela porta dos fundos).

Custo (RNF-04): so e chamado no ramo de KILL, que e raro; o caminho quente de
`Edit`/`Write` nunca le transcript. A leitura pega so o FIM do arquivo (o
transcript de uma sessao longa tem megabytes).
"""

from __future__ import annotations

import re

__all__ = ["ultimo_prompt_do_usuario", "kill_autorizado_pelo_usuario"]

_LIMITE_BYTES = 65_536
# Teto da busca para tras. Transcript de sessao longa passa de 10MB; varrer tudo
# num hook seria pior que perder o caso raro. 2MB cobre com folga um turno com
# dezenas de ferramentas -- e alem disso o `deny` (mais seguro) prevalece.
_TETO_BYTES = 2_097_152

# Verbos que expressam "encerrar um processo". Conservador de proposito: nao
# inclui "fecha" (fechar aba/arquivo/janela e outra coisa) nem "para" (parar
# tudo, parar de tentar), que apareceriam em pedidos sem relacao com processo.
_VERBO_KILL = re.compile(
    r"\b(kill|taskkill|tskill|pskill|stop-process|spps|"
    r"mat[ae]r?|mate|encerr\w*|derrub\w*|finaliz\w*)\b",
    re.IGNORECASE,
)


def ultimo_prompt_do_usuario(transcript_path: str, limite_bytes: int = _LIMITE_BYTES) -> str:
    """Texto da ultima fala REAL do usuario no transcript, ou "" se nao der.

    Le do FIM para tras em janelas que dobram, ate achar uma fala humana ou
    varrer o arquivo inteiro (teto em `_TETO_BYTES`). A janela unica de 64KB da
    primeira versao parecia suficiente e nao era: **a fala do usuario abre o
    turno, e tudo que vem depois — tool_use, tool_result, attachment — empurra
    ela para longe do fim.** Medido no ensaio: transcript de 183KB, pedido de
    kill explicito na primeira linha, janela de 64KB, resultado "" -> o gate
    manteve o `deny` sobre uma ordem direta. O caso comum (turno com varias
    ferramentas) era justamente o que ficava de fora.

    Nunca levanta: transcript ausente, truncado ou com JSON quebrado devolve ""
    -- e "" significa "nao sei", que mantem o `deny`.
    """
    if not transcript_path:
        return ""
    try:
        import os

        tamanho = os.path.getsize(transcript_path)
    except Exception:  # noqa: BLE001 - ausente/ilegivel: "nao sei"
        return ""

    janela = max(limite_bytes, 1)
    while True:
        texto = _varre_janela(transcript_path, tamanho, janela)
        if texto:
            return texto
        if janela >= tamanho or janela >= _TETO_BYTES:
            return ""
        janela *= 2


def _varre_janela(transcript_path: str, tamanho: int, janela: int) -> str:
    """Ultima fala humana nos ultimos `janela` bytes do arquivo."""
    try:
        import json

        with open(transcript_path, "rb") as fh:
            if tamanho > janela:
                fh.seek(tamanho - janela)
                fh.readline()  # descarta a linha cortada ao meio
            bruto = fh.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""

    ultimo = ""
    for linha in bruto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            d = json.loads(linha)
        except Exception:  # noqa: BLE001 - linha parcial
            continue
        if d.get("type") != "user":
            continue
        # `isMeta: True` = texto INJETADO pelo harness com role=user (feedback de
        # hook, avisos do sistema), nao coisa que o Vinicius digitou. Medido no
        # ensaio: o feedback do meu proprio hook de `Stop` era a ultima "fala do
        # usuario" do transcript e mascarava o pedido real. Pior que mascarar:
        # sem este filtro, um hook que imprimisse "mate o processo X" estaria
        # fabricando a autorizacao do usuario.
        if d.get("isMeta"):
            continue
        texto = _texto_de_fala_humana((d.get("message") or {}).get("content"))
        if texto:
            ultimo = texto
    return ultimo


def _texto_de_fala_humana(conteudo) -> str:
    """So o que o usuario DIGITOU.

    `tool_result` vem com `role="user"` no transcript (o harness devolve a saida
    da ferramenta como se fosse fala do usuario). Aceitar isso deixaria eu mesma
    fabricar a autorizacao: bastaria um comando cuja SAIDA contivesse o PID e o
    verbo.
    """
    if isinstance(conteudo, str):
        return conteudo
    if not isinstance(conteudo, list):
        return ""
    partes = []
    for c in conteudo:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text" and isinstance(c.get("text"), str):
            partes.append(c["text"])
    return "\n".join(partes)


def kill_autorizado_pelo_usuario(resource_id: str, texto_prompt) -> bool:
    """O usuario pediu, no turno, para matar ESTE recurso?

    Exige as duas metades -- verbo de kill E alvo casando com o recurso. Uma
    sozinha nao basta: "mata esse bug" e vontade sem alvo; "o chrome esta
    pesado" e alvo sem ordem.
    """
    if not resource_id or not texto_prompt or not isinstance(texto_prompt, str):
        return False
    if not _VERBO_KILL.search(texto_prompt):
        return False

    alvo = resource_id.split(":", 1)[1] if ":" in resource_id else resource_id
    alvo = alvo.strip()
    if not alvo:
        return False

    # Fronteira de palavra dos dois lados: `process:448` NAO pode casar por
    # estar dentro de `20448`, nem `chrome` dentro de `chromedriver`.
    padrao = re.compile(rf"(?<![\w~]){re.escape(alvo)}(?![\w~])", re.IGNORECASE)
    return bool(padrao.search(texto_prompt))
