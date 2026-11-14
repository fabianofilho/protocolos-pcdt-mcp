"""buscar_literatura: parsing do PubMed, rate limit e a tool."""

from __future__ import annotations

import httpx
import pytest
import respx

from galeno_mcp.literatura.base import ArtigoEncontrado, LimitadorTaxa
from galeno_mcp.literatura.pubmed import BASE, PubMed, PubMedIndisponivel, parse_efetch
from galeno_mcp.mcp_server.tools.literatura import buscar_literatura


def _esearch(ids: list[str]) -> httpx.Response:
    return httpx.Response(200, json={"esearchresult": {"idlist": ids, "count": str(len(ids))}})


# --- parse_efetch -----------------------------------------------------------


def test_parse_efetch_tipo_generico_sozinho_fica(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["11111111"].tipo_estudo == ["Journal Article"]


def test_parse_efetch_tipo_especifico_tira_o_generico(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["22222222"].tipo_estudo == ["Randomized Controlled Trial"]


def test_parse_efetch_ano_por_year(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["11111111"].ano == 2023
    assert artigos["22222222"].ano == 2022


def test_parse_efetch_ano_por_medline_date(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["33333333"].ano == 2019


def test_parse_efetch_sem_resumo_nem_doi(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["33333333"].resumo is None
    assert artigos["33333333"].doi is None


def test_parse_efetch_resumo_com_rotulos_junta_as_partes(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["22222222"].resumo == "Parte do objetivo. Parte dos resultados."


def test_parse_efetch_doi_quando_existe(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["11111111"].doi == "10.1000/teste.11111111"


def test_parse_efetch_url_do_pubmed(xml_efetch_literatura: str) -> None:
    artigos = {a.pmid: a for a in parse_efetch(xml_efetch_literatura)}
    assert artigos["11111111"].url == "https://pubmed.ncbi.nlm.nih.gov/11111111/"


def test_parse_efetch_xml_invalido() -> None:
    with pytest.raises(PubMedIndisponivel):
        parse_efetch("<isso nao fecha")


# --- ArtigoEncontrado.chave ---------------------------------------------------


def test_chave_prefere_doi() -> None:
    com_doi = ArtigoEncontrado(
        pmid="1",
        doi="10.1/ABC",
        fonte="pubmed",
        titulo="t",
        resumo=None,
        ano=None,
        tipo_estudo=[],
        url="u",
    )
    assert com_doi.chave == "10.1/abc"


def test_chave_sem_doi_usa_fonte_e_pmid() -> None:
    sem_doi = ArtigoEncontrado(
        pmid="12345",
        doi=None,
        fonte="pubmed",
        titulo="t",
        resumo=None,
        ano=None,
        tipo_estudo=[],
        url="u",
    )
    assert sem_doi.chave == "pubmed:12345"


# --- LimitadorTaxa -------------------------------------------------------------


async def test_limitador_zero_nao_espera() -> None:
    limitador = LimitadorTaxa(0)
    await limitador.esperar()  # não deve levantar nem travar


# --- PubMed (cliente) ----------------------------------------------------------


@respx.mock
async def test_pubmed_buscar_ids_usa_relevancia() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sort"] == "relevance"
        assert request.url.params["term"] == "asma"
        assert request.url.params["retmax"] == "5"
        return _esearch(["1", "2"])

    respx.get(f"{BASE}/esearch.fcgi").mock(side_effect=responder)
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        ids = await pubmed.buscar_ids("asma", limite=5)
    assert ids == ["1", "2"]


@respx.mock
async def test_pubmed_esearch_resposta_inesperada() -> None:
    respx.get(f"{BASE}/esearch.fcgi").mock(return_value=httpx.Response(200, json={}))
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    with pytest.raises(PubMedIndisponivel):
        async with pubmed:
            await pubmed.buscar_ids("asma", limite=5)


@respx.mock
async def test_pubmed_detalhes_sem_ids_nao_chama_a_api() -> None:
    rota = respx.get(f"{BASE}/efetch.fcgi").mock(return_value=httpx.Response(200, text=""))
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        artigos = await pubmed.detalhes([])
    assert artigos == []
    assert rota.call_count == 0


@respx.mock
async def test_pubmed_buscar_mantem_ordem_de_relevancia(xml_efetch_literatura: str) -> None:
    respx.get(f"{BASE}/esearch.fcgi").mock(
        return_value=_esearch(["33333333", "11111111", "22222222"])
    )
    respx.get(f"{BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=xml_efetch_literatura)
    )
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        artigos = await pubmed.buscar("teste", limite=3)
    assert [a.pmid for a in artigos] == ["33333333", "11111111", "22222222"]


@respx.mock
async def test_pubmed_api_key_entra_nos_parametros() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "chave-123"
        return _esearch([])

    respx.get(f"{BASE}/esearch.fcgi").mock(side_effect=responder)
    pubmed = PubMed(api_key="chave-123")
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        await pubmed.buscar_ids("asma", limite=5)


def test_pubmed_fora_do_async_with_levanta() -> None:
    pubmed = PubMed()
    with pytest.raises(RuntimeError):
        pubmed._exigir_client()


def test_pubmed_instancias_sem_chave_compartilham_o_limitador() -> None:
    """O limite é da NCBI por IP, não por instância: duas instâncias concorrentes
    (modo connector) têm que esperar a mesma fila, não uma fila cada."""
    a = PubMed()
    b = PubMed()
    assert a._limitador is b._limitador


def test_pubmed_instancias_com_chave_tem_limitador_proprio_e_mais_rapido() -> None:
    a = PubMed(api_key="x")
    b = PubMed(api_key="y")
    sem_chave = PubMed()
    assert a._limitador is b._limitador
    assert a._limitador is not sem_chave._limitador


# --- a tool buscar_literatura ---------------------------------------------------


async def test_buscar_literatura_termo_vazio() -> None:
    resposta = await buscar_literatura("   ")
    assert resposta.retornados == 0
    assert resposta.aviso == "Informe um termo de busca."


@respx.mock
async def test_buscar_literatura_sem_resultado() -> None:
    respx.get(f"{BASE}/esearch.fcgi").mock(return_value=_esearch([]))
    resposta = await buscar_literatura("termoqueNaoExisteXYZ")
    assert resposta.retornados == 0
    assert resposta.resultados == []
    assert "Nenhum artigo" in (resposta.aviso or "")


@respx.mock
async def test_buscar_literatura_pubmed_indisponivel_vira_aviso() -> None:
    respx.get(f"{BASE}/esearch.fcgi").mock(return_value=httpx.Response(200, json={}))
    resposta = await buscar_literatura("asma")
    assert resposta.resultados == []
    assert resposta.aviso == (
        "O PubMed não respondeu como esperado agora. Tente de novo em instantes."
    )


@respx.mock
async def test_buscar_literatura_falha_de_rede_vira_aviso() -> None:
    respx.get(f"{BASE}/esearch.fcgi").mock(side_effect=httpx.ConnectError("sem rede"))
    resposta = await buscar_literatura("asma")
    assert resposta.resultados == []
    assert resposta.aviso == (
        "O PubMed não respondeu como esperado agora. Tente de novo em instantes."
    )


@respx.mock
async def test_buscar_literatura_mapeia_os_campos(xml_efetch_literatura: str) -> None:
    respx.get(f"{BASE}/esearch.fcgi").mock(return_value=_esearch(["11111111"]))
    respx.get(f"{BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=xml_efetch_literatura)
    )
    resposta = await buscar_literatura("teste", limite=1)
    assert resposta.retornados == 1
    artigo = resposta.resultados[0]
    assert artigo.pmid == "11111111"
    assert artigo.doi == "10.1000/teste.11111111"
    assert artigo.fonte == "pubmed"
    assert artigo.ano == 2023
    assert artigo.tipo_estudo == ["Journal Article"]
    assert resposta.aviso is None
