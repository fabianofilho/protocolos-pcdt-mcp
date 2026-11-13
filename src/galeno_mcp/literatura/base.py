"""Modelo comum aos fetchers de literatura (PubMed, e no futuro Europe PMC)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ArtigoEncontrado:
    """Um artigo, normalizado independente da fonte."""

    pmid: str | None
    doi: str | None
    fonte: str
    titulo: str
    resumo: str | None
    ano: int | None
    tipo_estudo: list[str]
    url: str

    @property
    def chave(self) -> str:
        """DOI quando existe; senão o par fonte:identificador da fonte.

        Serve para deduplicar quando uma segunda fonte (Europe PMC) entrar: o
        mesmo artigo pode aparecer nas duas, com o mesmo DOI.
        """
        identificador = self.pmid or self.titulo
        return (self.doi or f"{self.fonte}:{identificador}").lower()


class LimitadorTaxa:
    """Espaça as requisições para respeitar o limite da API.

    A NCBI permite 3 requisições por segundo sem chave e 10 com chave. Passar
    disso devolve erro de rate limit, como acontece na prática.
    """

    def __init__(self, por_segundo: float) -> None:
        self._intervalo = 1.0 / por_segundo if por_segundo > 0 else 0.0
        self._ultima = 0.0

    async def esperar(self) -> None:
        if self._intervalo <= 0:
            return
        agora = time.monotonic()
        restante = self._intervalo - (agora - self._ultima)
        if restante > 0:
            await asyncio.sleep(restante)
        self._ultima = time.monotonic()
