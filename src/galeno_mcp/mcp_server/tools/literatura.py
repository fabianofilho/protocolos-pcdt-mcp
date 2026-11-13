"""A tool buscar_literatura: PubMed sob demanda, por enquanto."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from galeno_mcp.literatura.pubmed import PubMed, PubMedIndisponivel

logger = logging.getLogger(__name__)

AVISO_PUBMED_INDISPONIVEL = (
    "O PubMed não respondeu como esperado agora. Tente de novo em instantes."
)


class ArtigoLiteratura(BaseModel):
    pmid: str | None = None
    doi: str | None = None
    fonte: str
    titulo: str
    resumo: str | None = None
    ano: int | None = None
    tipo_estudo: list[str] = Field(default_factory=list)
    url: str


class RespostaLiteratura(BaseModel):
    termo: str
    retornados: int = 0
    resultados: list[ArtigoLiteratura] = Field(default_factory=list)
    aviso: str | None = None


async def buscar_literatura(
    termo: str,
    *,
    limite: int = 10,
    api_key: str | None = None,
) -> RespostaLiteratura:
    """Busca sob demanda no PubMed pelos artigos mais relevantes ao termo.

    Hoje só consulta o PubMed; Europe PMC fica para uma próxima versão desta
    tool. Os resultados vêm na ordem de relevância que o próprio PubMed
    calcula (``sort=relevance`` do esearch), não por data.
    """
    termo = termo.strip()
    if not termo:
        return RespostaLiteratura(termo=termo, aviso="Informe um termo de busca.")

    try:
        async with PubMed(api_key=api_key) as pubmed:
            artigos = await pubmed.buscar(termo, limite=limite)
    except PubMedIndisponivel as erro:
        logger.warning("PubMed indisponível: %s", erro)
        return RespostaLiteratura(termo=termo, aviso=AVISO_PUBMED_INDISPONIVEL)
    except Exception:  # noqa: BLE001 - falha de rede não derruba a tool
        logger.exception("falha ao buscar no PubMed")
        return RespostaLiteratura(termo=termo, aviso=AVISO_PUBMED_INDISPONIVEL)

    resultados = [
        ArtigoLiteratura(
            pmid=artigo.pmid,
            doi=artigo.doi,
            fonte=artigo.fonte,
            titulo=artigo.titulo,
            resumo=artigo.resumo,
            ano=artigo.ano,
            tipo_estudo=artigo.tipo_estudo,
            url=artigo.url,
        )
        for artigo in artigos
    ]
    aviso = None if resultados else "Nenhum artigo encontrado para esse termo no PubMed."
    return RespostaLiteratura(
        termo=termo, retornados=len(resultados), resultados=resultados, aviso=aviso
    )
