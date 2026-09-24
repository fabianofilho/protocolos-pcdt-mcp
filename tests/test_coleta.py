"""Coleta: parsing da tabela real e do CSV de status."""

from __future__ import annotations

import pytest

from protocolos_pcdt_mcp.coleta.downloader import CachePdf
from protocolos_pcdt_mcp.coleta.listagem import (
    ItemPcdt,
    ListagemIndisponivel,
    parse_listagem,
    parse_status_csv,
)
from protocolos_pcdt_mcp.nomes import chave_nome, separar_nota


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
    assert mapa[chave_nome("Acromegalia")] == "Aprovado*"
    assert mapa[chave_nome("Acidentes Ofídicos")] == "Conitec"


def test_status_casa_nome_com_nota_e_sem_acento(csv_status_zip: bytes) -> None:
    """O portal escreve "Acidentes Ofídicos (anexo alterado ...)"; o CSV, sem a nota."""
    mapa = parse_status_csv(csv_status_zip)
    assert mapa.get(chave_nome("Acidentes ofidicos (Anexo alterado em 01/02/2026)")) == "Conitec"


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


# --- nota de revisão no nome -----------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "nome", "nota"),
    [
        ("Asma (anexo alterado em 04/09/2026)", "Asma", "anexo alterado em 04/09/2026"),
        (
            "Hipertensão Pulmonar - alterado em 26/09/2024",
            "Hipertensão Pulmonar",
            "alterado em 26/09/2024",
        ),
        (
            "Osteoporose - portaria atualizada em 29/01/2026",
            "Osteoporose",
            "portaria atualizada em 29/01/2026",
        ),
        (
            "Fibrose Cística (portaria atualizada em 09/05/2024)",
            "Fibrose Cística",
            "portaria atualizada em 09/05/2024",
        ),
        (
            "Profilaxia (PEP) à Infecção pelo HIV, IST e Hepatites Virais. "
            "(anexo alterado em 20/03/2025)",
            "Profilaxia (PEP) à Infecção pelo HIV, IST e Hepatites Virais",
            "anexo alterado em 20/03/2025",
        ),
        # Hífen e parênteses que fazem parte do nome ficam.
        ("Síndrome de Guillain-Barré", "Síndrome de Guillain-Barré", None),
        (
            "Leucemia Mieloide Crônica - Crianças e Adolescentes",
            "Leucemia Mieloide Crônica - Crianças e Adolescentes",
            None,
        ),
        (
            "Degeneração Macular (forma neovascular)",
            "Degeneração Macular (forma neovascular)",
            None,
        ),
    ],
)
def test_separa_nota_de_revisao(bruto: str, nome: str, nota: str | None) -> None:
    assert separar_nota(bruto) == (nome, nota)


def test_identificador_nao_carrega_data_de_revisao() -> None:
    item = ItemPcdt(
        condicao="Asma (anexo alterado em 04/09/2026)",
        url_pdf=None,
        url_resumido=None,
        portaria=None,
        data_portaria=None,
    )
    assert item.identificador == "asma"


def test_listagem_separa_nota_do_nome() -> None:
    html = """<table>
      <tr><td><a href="/conitec/asma.pdf">Asma (anexo alterado em 04/09/2026)</a></td>
          <td>Portaria Conjunta nº 43 - 24/03/2026</td></tr>
    </table>"""
    [item] = parse_listagem(html)
    assert item.condicao == "Asma"
    assert item.identificador == "asma"
    assert item.nota_atualizacao == "anexo alterado em 04/09/2026"
