"""Coleta: parsing da tabela real e do CSV de status."""

from __future__ import annotations

import pytest

from protocolos_pcdt_mcp.coleta.downloader import CachePdf
from protocolos_pcdt_mcp.coleta.listagem import (
    ListagemIndisponivel,
    parse_listagem,
    parse_status_csv,
)


def test_parse_da_tabela_real(html_listagem: str) -> None:
    itens = parse_listagem(html_listagem)
    assert itens
    primeiro = itens[0]
    assert primeiro.condicao
    assert primeiro.url_pdf is not None and primeiro.url_pdf.startswith("https://www.gov.br")


def test_separador_alfabetico_nao_vira_protocolo(html_listagem: str) -> None:
    """A tabela tem linhas com uma letra só (A, B, C) como índice."""
    itens = parse_listagem(html_listagem)
    assert all(len(item.condicao) > 1 for item in itens)


def test_extrai_data_da_portaria(html_listagem: str) -> None:
    itens = parse_listagem(html_listagem)
    assert any(item.data_portaria is not None for item in itens)


def test_pcdt_resumido_e_capturado(html_listagem: str) -> None:
    itens = parse_listagem(html_listagem)
    assert any(item.url_resumido for item in itens)


def test_identificador_normaliza_espacos() -> None:
    from protocolos_pcdt_mcp.coleta.listagem import ItemPcdt

    item = ItemPcdt(
        condicao="  Diabetes   Mellitus  Tipo 1 ",
        url_pdf=None,
        url_resumido=None,
        portaria=None,
        data_portaria=None,
    )
    assert item.identificador == "diabetes mellitus tipo 1"


def test_pagina_sem_tabela_falha_explicito() -> None:
    with pytest.raises(ListagemIndisponivel) as erro:
        parse_listagem("<html><body>nada</body></html>")
    assert "portal pode ter mudado" in str(erro.value)


def test_status_csv(csv_status_zip: bytes) -> None:
    mapa = parse_status_csv(csv_status_zip)
    assert mapa["acromegalia"] == "Aprovado*"
    assert mapa["acidentes ofídicos"] == "Conitec"


def test_status_csv_invalido_falha_explicito() -> None:
    with pytest.raises(ListagemIndisponivel):
        parse_status_csv(b"isso nao e um zip")


def test_cache_nao_rebaixa(tmp_path: object) -> None:
    from pathlib import Path

    cache = CachePdf(Path(str(tmp_path)))
    url = "https://www.gov.br/conitec/pcdt.pdf"
    assert cache.tem(url) is False
    cache.gravar(url, b"%PDF-1.4")
    assert cache.ler(url) == b"%PDF-1.4"
