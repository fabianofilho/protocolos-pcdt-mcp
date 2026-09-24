"""Migração dos identificadores que carregavam nota de revisão."""

from __future__ import annotations

from pathlib import Path

import duckdb

from protocolos_pcdt_mcp.store.db import aplicar_schema, conectar
from protocolos_pcdt_mcp.store.queries import por_identificador

ANTIGO = "asma (anexo alterado em 04/09/2026)"

# DDL da versão 1, sem a coluna nota_atualizacao.
_DDL_V1 = """
CREATE TABLE protocolos (
    identificador VARCHAR PRIMARY KEY, condicao VARCHAR NOT NULL, status VARCHAR,
    portaria VARCHAR, data_portaria DATE, url_pdf VARCHAR, url_resumido VARCHAR,
    texto_completo VARCHAR, secoes_json VARCHAR, vigente BOOLEAN NOT NULL DEFAULT TRUE,
    substituido_por VARCHAR, extracao_incompleta BOOLEAN NOT NULL DEFAULT FALSE,
    data_coleta TIMESTAMP DEFAULT current_timestamp
)
"""


def _base_v1(caminho: Path) -> None:
    conexao = duckdb.connect(str(caminho))
    conexao.execute(_DDL_V1)
    conexao.execute(
        "INSERT INTO protocolos (identificador, condicao, url_pdf, texto_completo) "
        "VALUES (?, ?, 'https://x/asma.pdf', 'texto da asma'), "
        "('glaucoma', 'Glaucoma', 'https://x/g.pdf', 'texto do glaucoma')",
        [ANTIGO, "Asma (anexo alterado em 04/09/2026)"],
    )
    conexao.close()


def test_base_v1_migra_preservando_texto(tmp_path: Path) -> None:
    caminho = tmp_path / "v1.duckdb"
    _base_v1(caminho)
    with conectar(caminho) as conexao:
        linhas = conexao.execute(
            "SELECT identificador, condicao, nota_atualizacao, texto_completo FROM protocolos "
            "ORDER BY identificador"
        ).fetchall()
    assert linhas == [
        ("asma", "Asma", "anexo alterado em 04/09/2026", "texto da asma"),
        ("glaucoma", "Glaucoma", None, "texto do glaucoma"),
    ]


def test_migracao_e_idempotente(tmp_path: Path) -> None:
    caminho = tmp_path / "v1.duckdb"
    _base_v1(caminho)
    with conectar(caminho):
        pass
    with conectar(caminho) as conexao:
        total = conexao.execute("SELECT count(*) FROM protocolos").fetchone()
    assert total == (2,)


def test_migracao_com_registro_limpo_ja_existente(db: duckdb.DuckDBPyConnection) -> None:
    """Se um sync já criou 'asma' sem texto, o texto do antigo passa para ele."""
    db.execute(
        "INSERT INTO protocolos (identificador, condicao, texto_completo) VALUES "
        "(?, 'Asma (anexo alterado em 04/09/2026)', 'texto antigo'), ('asma', 'Asma', NULL)",
        [ANTIGO],
    )
    aplicar_schema(db)
    assert db.execute("SELECT identificador, texto_completo FROM protocolos").fetchall() == [
        ("asma", "texto antigo")
    ]


def test_por_identificador_aceita_id_sem_nota(db: duckdb.DuckDBPyConnection) -> None:
    """Base ainda não migrada (servidor lendo em read_only antes do próximo sync)."""
    db.execute(
        "INSERT INTO protocolos (identificador, condicao) VALUES "
        "(?, 'Asma (anexo alterado em 04/09/2026)')",
        [ANTIGO],
    )
    linha = por_identificador(db, "asma")
    assert linha is not None and linha["identificador"] == ANTIGO
    assert por_identificador(db, "Asma") is not None
    assert por_identificador(db, "rinite") is None
