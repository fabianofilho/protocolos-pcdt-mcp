"""Queries sobre a base de protocolos."""

from __future__ import annotations

import logging
from typing import Any

import duckdb

from protocolos_pcdt_mcp.nomes import chave_nome

logger = logging.getLogger(__name__)

_COLUNAS = (
    "identificador, condicao, status, portaria, data_portaria, url_pdf, url_resumido, "
    "texto_completo, secoes_json, vigente, substituido_por, extracao_incompleta, "
    "nota_atualizacao"
)


def _para_dicts(resultado: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    colunas = [d[0] for d in resultado.description or []]
    return [dict(zip(colunas, linha, strict=True)) for linha in resultado.fetchall()]


def buscar_condicao(
    conexao: duckdb.DuckDBPyConnection,
    termo: str,
    *,
    limite: int = 10,
    apenas_vigentes: bool = True,
) -> list[dict[str, Any]]:
    """Busca por doença ou condição, tolerante a grafia.

    Casa por substring sem acento e sem caixa, e ordena pelo tamanho do nome,
    "diabetes" traz "Diabetes Mellitus Tipo 1" antes de nomes longos que apenas
    citam a condição.
    """
    filtro = "AND vigente" if apenas_vigentes else ""
    padrao = f"%{termo.strip()}%"
    return _para_dicts(
        conexao.execute(
            f"""
            SELECT {_COLUNAS}
            FROM protocolos
            WHERE strip_accents(lower(condicao)) LIKE strip_accents(lower(?))
              {filtro}
            ORDER BY length(condicao), condicao
            LIMIT ?
            """,
            [padrao, limite],
        )
    )


def buscar_no_texto(
    conexao: duckdb.DuckDBPyConnection,
    termo: str,
    *,
    limite: int = 10,
) -> list[dict[str, Any]]:
    """Busca no texto completo, via FTS quando disponível."""
    try:
        return _para_dicts(
            conexao.execute(
                f"""
                SELECT {_COLUNAS}, fts_main_protocolos.match_bm25(identificador, ?) AS relevancia
                FROM protocolos
                WHERE relevancia IS NOT NULL AND vigente
                ORDER BY relevancia DESC
                LIMIT ?
                """,
                [termo, limite],
            )
        )
    except duckdb.Error as erro:
        logger.debug("FTS indisponível (%s); usando LIKE", erro)

    padrao = f"%{termo.strip()}%"
    return _para_dicts(
        conexao.execute(
            f"""
            SELECT {_COLUNAS}, NULL AS relevancia
            FROM protocolos
            WHERE lower(coalesce(texto_completo, '')) LIKE lower(?) AND vigente
            ORDER BY length(condicao)
            LIMIT ?
            """,
            [padrao, limite],
        )
    )


def por_identificador(
    conexao: duckdb.DuckDBPyConnection, identificador: str
) -> dict[str, Any] | None:
    """Protocolo pelo identificador exato ou, se não houver, pelo nome sem nota.

    O segundo caminho aceita "asma" quando a base ainda guarda "asma (anexo
    alterado em ...)", e também diferenças de acento e pontuação. Entre vários
    candidatos, prefere o vigente.
    """
    linhas = _para_dicts(
        conexao.execute(
            f"SELECT {_COLUNAS} FROM protocolos WHERE identificador = ?", [identificador]
        )
    )
    if linhas:
        return linhas[0]

    alvo = chave_nome(identificador)
    if not alvo:
        return None
    candidatos = conexao.execute(
        "SELECT identificador, condicao FROM protocolos ORDER BY vigente DESC, identificador"
    ).fetchall()
    for ident, condicao in candidatos:
        if alvo in (chave_nome(ident), chave_nome(condicao)):
            return por_identificador(conexao, ident)
    return None


def gravar(conexao: duckdb.DuckDBPyConnection, registros: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert em massa. Devolve (novos, atualizados)."""
    if not registros:
        return 0, 0

    unicos = {r["identificador"]: r for r in registros}
    linhas = list(unicos.values())
    colunas = list(linhas[0].keys())
    lista = ", ".join(colunas)
    marcadores = ", ".join("?" for _ in colunas)
    atribuicoes = ", ".join(f"{c} = excluded.{c}" for c in colunas if c != "identificador")

    staging = "staging_protocolos"
    conexao.execute(
        f"CREATE OR REPLACE TEMP TABLE {staging} AS SELECT {lista} FROM protocolos LIMIT 0"
    )
    conexao.executemany(
        f"INSERT INTO {staging} ({lista}) VALUES ({marcadores})",
        [[r[c] for c in colunas] for r in linhas],
    )
    contagem = conexao.execute(
        f"""
        SELECT count(*) FROM {staging} s
        WHERE NOT EXISTS (
            SELECT 1 FROM protocolos p WHERE p.identificador = s.identificador
        )
        """
    ).fetchone()
    novos = int(contagem[0]) if contagem else 0

    conexao.execute(
        f"""
        INSERT INTO protocolos ({lista})
        SELECT {lista} FROM {staging}
        ON CONFLICT (identificador) DO UPDATE SET {atribuicoes}, data_coleta = now()
        """
    )
    conexao.execute(f"DROP TABLE {staging}")
    return novos, len(linhas) - novos


def marcar_ausentes_como_substituidos(
    conexao: duckdb.DuckDBPyConnection, identificadores_vistos: list[str]
) -> int:
    """Protocolo que sumiu da listagem deixou de ser vigente.

    Não apaga: a spec pede manter histórico, e um PCDT removido ainda é útil
    para entender o que valia antes.
    """
    if not identificadores_vistos:
        return 0
    marcadores = ", ".join("?" for _ in identificadores_vistos)
    resultado = conexao.execute(
        f"""
        UPDATE protocolos SET vigente = FALSE
        WHERE vigente AND identificador NOT IN ({marcadores})
        """,
        identificadores_vistos,
    )
    linhas = resultado.fetchall()
    return int(linhas[0][0]) if linhas and linhas[0] else 0
