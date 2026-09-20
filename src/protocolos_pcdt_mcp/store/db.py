"""DuckDB com FTS sobre condição e texto do protocolo."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSAO = 1

_DDL = """
CREATE TABLE IF NOT EXISTS protocolos (
    identificador       VARCHAR PRIMARY KEY,
    condicao            VARCHAR NOT NULL,
    status              VARCHAR,
    portaria            VARCHAR,
    data_portaria       DATE,
    url_pdf             VARCHAR,
    url_resumido        VARCHAR,
    texto_completo      VARCHAR,
    secoes_json         VARCHAR,
    vigente             BOOLEAN NOT NULL DEFAULT TRUE,
    substituido_por     VARCHAR,
    extracao_incompleta BOOLEAN NOT NULL DEFAULT FALSE,
    data_coleta         TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS schema_meta (
    chave VARCHAR PRIMARY KEY,
    valor VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prot_condicao ON protocolos (condicao);
CREATE INDEX IF NOT EXISTS idx_prot_vigente ON protocolos (vigente);
"""


class BaseIndisponivel(RuntimeError):
    """A base existe mas está travada — tipicamente uma coleta em curso."""


def _instalar_fts(conexao: duckdb.DuckDBPyConnection) -> bool:
    try:
        conexao.execute("INSTALL fts")
        conexao.execute("LOAD fts")
        return True
    except duckdb.Error as erro:
        logger.warning("FTS indisponível (%s); a busca cai para LIKE", erro)
        return False


def reindexar_fts(conexao: duckdb.DuckDBPyConnection) -> bool:
    """(Re)cria o índice FTS. Precisa rodar depois de cada carga."""
    if not _instalar_fts(conexao):
        return False
    try:
        conexao.execute(
            "PRAGMA create_fts_index('protocolos', 'identificador', 'condicao', "
            "'texto_completo', overwrite=1)"
        )
        return True
    except duckdb.Error as erro:
        logger.warning("não consegui criar o índice FTS: %s", erro)
        return False


def aplicar_schema(conexao: duckdb.DuckDBPyConnection) -> None:
    conexao.execute(_DDL)
    conexao.execute(
        "INSERT INTO schema_meta VALUES ('versao', ?) "
        "ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor",
        [str(SCHEMA_VERSAO)],
    )


@contextmanager
def conectar(
    caminho: Path | str,
    *,
    somente_leitura: bool = False,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre o DuckDB; em leitura não cria nada."""
    em_memoria = str(caminho) == ":memory:"
    if somente_leitura and not em_memoria and not Path(caminho).exists():
        raise FileNotFoundError(f"base local ainda não existe em {caminho}")
    if not somente_leitura and not em_memoria:
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)

    try:
        conexao = duckdb.connect(str(caminho), read_only=somente_leitura and not em_memoria)
    except duckdb.IOException as erro:
        raise BaseIndisponivel(str(erro)) from erro

    try:
        if not somente_leitura:
            aplicar_schema(conexao)
        else:
            _instalar_fts(conexao)
        yield conexao
    finally:
        conexao.close()
