"""Cliente sob demanda da API E-utilities do PubMed.

esearch devolve os IDs por relevância, efetch devolve o detalhe em XML. O rate
limit da NCBI é de 3 requisições por segundo sem chave e 10 com chave; passar
disso devolve ``{"error": "API rate limit exceeded"}``.

Mesma API e desenho de base do fetcher do radar-papers-mcp (mesmo autor), mas
sob demanda por termo e relevância, não por data de entrada: aqui quem
pergunta quer achar o artigo certo agora, não saber o que é novo.
"""

from __future__ import annotations

import logging
from xml.etree import ElementTree

import httpx

from galeno_mcp.coleta.listagem import USER_AGENT
from galeno_mcp.literatura.base import ArtigoEncontrado, LimitadorTaxa

logger = logging.getLogger(__name__)

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FONTE = "pubmed"

# Tipo genérico demais para valer sozinho como "tipo de estudo": todo artigo de
# revista ganha "Journal Article" do PubMed, o que não diz nada do desenho do
# estudo. Só fica na resposta quando é o ÚNICO tipo do artigo.
_TIPO_GENERICO = "Journal Article"


class PubMedIndisponivel(RuntimeError):
    """A API não respondeu como esperado."""


def _texto(elemento: ElementTree.Element | None) -> str | None:
    if elemento is None:
        return None
    texto = "".join(elemento.itertext()).strip()
    return texto or None


def _ano(artigo: ElementTree.Element) -> int | None:
    """Ano da publicação. ``MedlineDate`` cobre os casos sem Year estruturado
    (ex. "2019 Winter", "2019-2020"): pega os 4 primeiros dígitos que achar.
    """
    for caminho in (
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year",
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate",
    ):
        texto = _texto(artigo.find(caminho))
        if not texto:
            continue
        digitos = "".join(c for c in texto[:9] if c.isdigit())
        if len(digitos) >= 4:
            try:
                return int(digitos[:4])
            except ValueError:
                continue
    return None


def _tipos_de_estudo(artigo: ElementTree.Element) -> list[str]:
    tipos = [
        texto
        for no in artigo.findall(".//PublicationTypeList/PublicationType")
        if (texto := _texto(no))
    ]
    especificos = [t for t in tipos if t != _TIPO_GENERICO]
    return especificos or tipos


def parse_efetch(xml: str) -> list[ArtigoEncontrado]:
    """Converte o XML do efetch em artigos normalizados."""
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as erro:
        raise PubMedIndisponivel("resposta do efetch não era XML válido") from erro

    artigos: list[ArtigoEncontrado] = []
    for no in raiz.findall(".//PubmedArticle"):
        pmid = _texto(no.find("./MedlineCitation/PMID"))
        titulo = _texto(no.find(".//ArticleTitle"))
        if not pmid or not titulo:
            continue

        doi = None
        for identificador in no.findall(".//ArticleId"):
            if identificador.get("IdType") == "doi":
                doi = _texto(identificador)
                break

        resumo_partes = [_texto(p) or "" for p in no.findall(".//Abstract/AbstractText")]

        artigos.append(
            ArtigoEncontrado(
                pmid=pmid,
                doi=doi,
                fonte=FONTE,
                titulo=titulo,
                resumo=" ".join(p for p in resumo_partes if p) or None,
                ano=_ano(no),
                tipo_estudo=_tipos_de_estudo(no),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            )
        )
    return artigos


class PubMed:
    """esearch + efetch, respeitando o rate limit, para busca sob demanda."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key or None
        self._limitador = LimitadorTaxa(10.0 if self._api_key else 3.0)
        self._client = client
        self._client_proprio = client is None

    async def __aenter__(self) -> PubMed:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client_proprio:
            await self._client.aclose()
            self._client = None

    def _params(self, extra: dict[str, str]) -> dict[str, str]:
        params = {"db": "pubmed", **extra}
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    def _exigir_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PubMed precisa ser usado como 'async with'")
        return self._client

    async def buscar_ids(self, termo: str, *, limite: int) -> list[str]:
        """Até ``limite`` PMIDs mais relevantes para o termo."""
        await self._limitador.esperar()
        resposta = await self._exigir_client().get(
            f"{BASE}/esearch.fcgi",
            params=self._params(
                {
                    "term": termo,
                    "retmode": "json",
                    "retmax": str(limite),
                    "sort": "relevance",
                }
            ),
        )
        resposta.raise_for_status()
        corpo = resposta.json()
        if "esearchresult" not in corpo:
            raise PubMedIndisponivel(f"esearch devolveu algo inesperado: {str(corpo)[:120]}")
        return [str(i) for i in corpo["esearchresult"].get("idlist", [])]

    async def detalhes(self, ids: list[str]) -> list[ArtigoEncontrado]:
        """Detalhe de até ~200 ids por chamada (o teto de ``limite`` já cobre isso)."""
        if not ids:
            return []
        await self._limitador.esperar()
        resposta = await self._exigir_client().get(
            f"{BASE}/efetch.fcgi",
            params=self._params({"id": ",".join(ids), "retmode": "xml"}),
        )
        resposta.raise_for_status()
        return parse_efetch(resposta.text)

    async def buscar(self, termo: str, *, limite: int) -> list[ArtigoEncontrado]:
        """Busca por termo; devolve na mesma ordem de relevância do esearch."""
        ids = await self.buscar_ids(termo, limite=limite)
        por_pmid = {artigo.pmid: artigo for artigo in await self.detalhes(ids)}
        return [por_pmid[i] for i in ids if i in por_pmid]
