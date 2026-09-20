"""Fixtures compartilhadas."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from protocolos_pcdt_mcp.store.db import aplicar_schema

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def html_listagem() -> str:
    """Recorte real da tabela de PCDTs da Conitec (capturado em 2026-09-20)."""
    return (FIXTURES / "listagem_conitec.html").read_text(encoding="utf-8")


@pytest.fixture
def csv_status_zip() -> bytes:
    """Zip no mesmo formato do CSV de dados abertos do Ministério da Saúde."""
    conteudo = (
        '"nome";"status";"tipo"\n'
        '"Acidentes Ofídicos";"Conitec";"PCDT"\n'
        '"Acromegalia";"Aprovado*";"PCDT"\n'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as arquivo:
        arquivo.writestr("pcdt.csv", conteudo)
    return buffer.getvalue()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    conexao = duckdb.connect(str(tmp_path / "teste.duckdb"))
    aplicar_schema(conexao)
    yield conexao
    conexao.close()


@pytest.fixture
def caminho_db(tmp_path: Path) -> str:
    return str(tmp_path / "tools.duckdb")
