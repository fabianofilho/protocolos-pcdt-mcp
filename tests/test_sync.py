"""Coleta ponta a ponta, com o portal mockado."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
import respx

from protocolos_pcdt_mcp.coleta import sync
from protocolos_pcdt_mcp.coleta.listagem import URL_LISTAGEM, URL_STATUS_CSV
from protocolos_pcdt_mcp.extract.parser import ProtocoloExtraido

GOVBR = "https://www.gov.br/conitec/"


def _html(linhas: list[tuple[str, str, str]]) -> str:
    corpo = "".join(
        f'<tr><td><a href="{GOVBR}{pdf}">{nome}</a></td><td>{portaria}</td></tr>'
        for nome, pdf, portaria in linhas
    )
    return f"<table>{corpo}</table>"


def _extrair_falso(conteudo: bytes) -> ProtocoloExtraido:
    return ProtocoloExtraido(texto=conteudo.decode(), paginas=1)


@pytest.fixture(autouse=True)
def _sem_pdfplumber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "extrair", _extrair_falso)


def _mock_portal(linhas: list[tuple[str, str, str]], pdfs: dict[str, bytes]) -> None:
    respx.get(URL_LISTAGEM).mock(return_value=httpx.Response(200, text=_html(linhas)))
    respx.get(URL_STATUS_CSV).mock(return_value=httpx.Response(404))
    for pdf, conteudo in pdfs.items():
        respx.get(f"{GOVBR}{pdf}").mock(return_value=httpx.Response(200, content=conteudo))


async def _coletar(db: duckdb.DuckDBPyConnection, tmp_path: Path, **kw: Any) -> Any:
    return await sync.coletar(db, delay_segundos=0, diretorio_cache=tmp_path / "pdfs", **kw)


def _texto(db: duckdb.DuckDBPyConnection, identificador: str) -> Any:
    linha = db.execute(
        "SELECT texto_completo, url_pdf FROM protocolos WHERE identificador = ?", [identificador]
    ).fetchone()
    return linha


@respx.mock
async def test_texto_e_preservado_quando_o_documento_nao_muda(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    linhas = [("Asma", "asma_v1.pdf", "Portaria nº 1 - 01/01/2026")]
    _mock_portal(linhas, {"asma_v1.pdf": b"texto v1"})
    await _coletar(db, tmp_path)
    resultado = await _coletar(db, tmp_path)
    assert resultado.pdfs_baixados == 0
    assert _texto(db, "asma")[0] == "texto v1"


@respx.mock
async def test_texto_e_reextraido_quando_a_url_do_pdf_muda(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Bug real: o upsert trocava url_pdf e mantinha o texto do PDF antigo."""
    _mock_portal([("Asma", "asma_v1.pdf", "Portaria nº 1 - 01/01/2026")], {"asma_v1.pdf": b"v1"})
    await _coletar(db, tmp_path)

    respx.reset()
    _mock_portal([("Asma", "asma_v2.pdf", "Portaria nº 9 - 01/06/2026")], {"asma_v2.pdf": b"v2"})
    await _coletar(db, tmp_path)
    assert _texto(db, "asma") == ("v2", f"{GOVBR}asma_v2.pdf")


@respx.mock
async def test_mesma_url_com_portaria_nova_ignora_o_cache(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _mock_portal([("Asma", "asma.pdf", "Portaria nº 1 - 01/01/2026")], {"asma.pdf": b"v1"})
    await _coletar(db, tmp_path)

    respx.reset()
    _mock_portal([("Asma", "asma.pdf", "Portaria nº 9 - 01/06/2026")], {"asma.pdf": b"v2"})
    resultado = await _coletar(db, tmp_path)
    assert resultado.pdfs_baixados == 1
    assert _texto(db, "asma")[0] == "v2"


@respx.mock
async def test_documento_novo_sem_cota_fica_sem_texto_em_vez_de_texto_velho(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _mock_portal([("Asma", "asma_v1.pdf", "Portaria nº 1 - 01/01/2026")], {"asma_v1.pdf": b"v1"})
    await _coletar(db, tmp_path)

    respx.reset()
    _mock_portal([("Asma", "asma_v2.pdf", "Portaria nº 9 - 01/06/2026")], {"asma_v2.pdf": b"v2"})
    await _coletar(db, tmp_path, max_pdfs=0)
    assert _texto(db, "asma") == (None, f"{GOVBR}asma_v2.pdf")


ASMA_V1 = [("Asma (anexo alterado em 01/01/2026)", "asma.pdf", "Portaria nº 1 - 01/01/2026")]
ASMA_V2 = [("Asma (anexo alterado em 04/09/2026)", "asma.pdf", "Portaria nº 1 - 01/01/2026")]


def _asma(db: duckdb.DuckDBPyConnection) -> Any:
    return db.execute(
        "SELECT texto_completo, nota_atualizacao FROM protocolos WHERE identificador = 'asma'"
    ).fetchone()


@respx.mock
async def test_mesma_url_sem_cota_nao_reextrai_o_pdf_velho_do_cache_depois(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Bug real: sem texto, o sync seguinte reextraía o PDF velho do cache com a nota nova."""
    _mock_portal(ASMA_V1, {"asma.pdf": b"velho"})
    await _coletar(db, tmp_path)

    respx.reset()
    _mock_portal(ASMA_V2, {"asma.pdf": b"novo"})
    await _coletar(db, tmp_path, max_pdfs=0)
    assert _asma(db) == (None, "anexo alterado em 04/09/2026")
    await _coletar(db, tmp_path, max_pdfs=0)
    assert _asma(db)[0] is None
    await _coletar(db, tmp_path)
    assert _asma(db) == ("novo", "anexo alterado em 04/09/2026")


@respx.mock
async def test_mesma_url_com_download_falho_nao_reextrai_o_pdf_velho_depois(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _mock_portal(ASMA_V1, {"asma.pdf": b"velho"})
    await _coletar(db, tmp_path)

    respx.reset()
    _mock_portal(ASMA_V2, {})
    respx.get(f"{GOVBR}asma.pdf").mock(return_value=httpx.Response(503))
    await _coletar(db, tmp_path)
    assert _asma(db)[0] is None

    respx.reset()
    _mock_portal(ASMA_V2, {"asma.pdf": b"novo"})
    await _coletar(db, tmp_path)
    assert _asma(db)[0] == "novo"


@respx.mock
async def test_documento_que_mudou_tem_prioridade_na_cota(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _mock_portal([("Asma", "asma_v1.pdf", "Portaria nº 1 - 01/01/2026")], {"asma_v1.pdf": b"v1"})
    await _coletar(db, tmp_path)

    respx.reset()
    _mock_portal(
        [
            ("Acromegalia", "acro.pdf", "Portaria nº 2 - 01/01/2026"),
            ("Asma", "asma_v2.pdf", "Portaria nº 9 - 01/06/2026"),
        ],
        {"acro.pdf": b"acro", "asma_v2.pdf": b"v2"},
    )
    await _coletar(db, tmp_path, max_pdfs=1)
    assert _texto(db, "asma")[0] == "v2"
    assert _texto(db, "acromegalia")[0] is None


def _listagem(n: int) -> list[tuple[str, str, str]]:
    return [(f"Condicao {i:03d}", f"c{i}.pdf", "Portaria nº 1 - 01/01/2026") for i in range(n)]


@respx.mock
async def test_listagem_parcial_nao_despromove_ninguem(
    db: duckdb.DuckDBPyConnection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _mock_portal(_listagem(10), {})
    await _coletar(db, tmp_path, max_pdfs=0)

    respx.reset()
    _mock_portal(_listagem(3), {})
    resultado = await _coletar(db, tmp_path, max_pdfs=0)
    assert resultado.despromovidos == 0
    assert db.execute("SELECT count(*) FROM protocolos WHERE vigente").fetchone() == (10,)
    assert "ninguém foi despromovido" in caplog.text


@respx.mock
async def test_protocolo_que_sai_da_listagem_completa_e_despromovido(
    db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _mock_portal(_listagem(10), {})
    await _coletar(db, tmp_path, max_pdfs=0)

    respx.reset()
    _mock_portal(_listagem(9), {})
    resultado = await _coletar(db, tmp_path, max_pdfs=0)
    assert resultado.despromovidos == 1
    assert db.execute("SELECT count(*) FROM protocolos WHERE vigente").fetchone() == (9,)
