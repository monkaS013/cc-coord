"""ccoord.paths - resolucao de nome curto 8.3 do Windows.

FONTE UNICA, pelo mesmo motivo de `ccoord.carimbos`: quem grava um claim e quem
le um carimbo tem de derivar a MESMA chave do mesmo arquivo. Se `classify`
resolvesse `VINICI~1` -> `ViniciusMoraisHDT` e `carimbos.slug_path` nao, o eco
da escrita propria nunca casaria e a sessao receberia aviso falso de "mudou em
disco por outro processo" -- o defeito da 4a auditoria, ressuscitado por outra
via. (Foi exatamente o que aconteceu: 7 testes de entrypoint quebraram quando a
resolucao entrou so no `classify`.)

Modulo minusculo de proposito: os hooks pagam o import dele no caminho quente
(RNF-04), entao nada de dependencia pesada no topo -- `ctypes` so e importado
dentro da funcao, e so quando ha um segmento 8.3 para resolver.
"""

from __future__ import annotations

import re

__all__ = ["resolver_nome_curto"]

_NOME_CURTO_8_3 = re.compile(r"~\d")
_CACHE_NOME_LONGO: dict = {}


def resolver_nome_curto(path: str) -> str:
    r"""`C:\Users\VINICI~1\...` -> `C:\Users\ViniciusMoraisHDT\...`.

    UNICA consulta ao SO no caminho de classificacao, e ela existe porque o
    ENSAIO com duas sessoes reais (12/09) mediu o gate CEGO: uma sessao
    segurava `...VINICI~1...\compartilhado.py` e a outra editava
    `...ViniciusMoraisHDT...\compartilhado.py`. `os.path.samefile` dizia True e
    os ids eram diferentes, entao nenhuma via a outra. Nao e hipotetico: o
    diretorio de scratchpad entregue a cada sessao vem no formato 8.3, entao
    valeria para quase toda sessao.

    Custo controlado: so chama o SO quando o caminho tem um segmento
    `~<digito>`, e memoiza. Sem `~N` a funcao e identidade pura, entao o
    determinismo do resto do modulo fica intacto. Se o caminho nao existe
    (arquivo prestes a ser criado), resolve o DIRETORIO pai e recola o nome --
    e se nem isso resolver, devolve o original em vez de inventar.

    Parente ainda ABERTO, de proposito: unidade mapeada (`Z:\`) x UNC continua
    dando ids diferentes. Aquilo exigiria consultar o mapeamento de rede, que
    muda com o tempo, e nao apareceu em uso real; este apareceu.
    """
    if not path or not _NOME_CURTO_8_3.search(path):
        return path
    em_cache = _CACHE_NOME_LONGO.get(path)
    if em_cache is not None:
        return em_cache

    resolvido = path
    try:
        import ctypes
        import os

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(path, buf, len(buf))
        if 0 < n < len(buf) and buf.value:
            resolvido = buf.value
        else:
            pai, nome = os.path.split(path)
            if pai and nome:
                n = ctypes.windll.kernel32.GetLongPathNameW(pai, buf, len(buf))
                if 0 < n < len(buf) and buf.value:
                    resolvido = os.path.join(buf.value, nome)
    except Exception:  # noqa: BLE001 - caminho invalido / SO sem a API
        resolvido = path

    _CACHE_NOME_LONGO[path] = resolvido
    return resolvido
