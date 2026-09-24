"""Coleta: listagem, status, PDFs e extração."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from protocolos_pcdt_mcp.coleta.downloader import CachePdf, Downloader
from protocolos_pcdt_mcp.coleta.listagem import baixar_listagem, baixar_status
from protocolos_pcdt_mcp.extract.parser import extrair
from protocolos_pcdt_mcp.nomes import chave_nome
from protocolos_pcdt_mcp.store.queries import gravar, marcar_ausentes_como_substituidos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoColeta:
    novos: int
    atualizados: int
    pdfs_baixados: int
    despromovidos: int


async def coletar(
    conexao: duckdb.DuckDBPyConnection,
    *,
    delay_segundos: float = 1.0,
    max_pdfs: int = 20,
    diretorio_cache: Path | str = "./data/pdfs",
) -> ResultadoColeta:
    """Atualiza a base a partir do portal da Conitec.

    O texto completo só é baixado para ``max_pdfs`` protocolos por execução: são
    ~150 PDFs grandes, e o objetivo é a base ficar completa ao longo de algumas
    noites, não sobrecarregar o portal numa única.
    """
    itens = await baixar_listagem()
    status = await baixar_status()
    logger.info("PCDT: %d protocolos na listagem, %d com status", len(itens), len(status))

    cache = CachePdf(diretorio_cache)
    baixados = 0
    registros: list[dict[str, Any]] = []

    ja_com_texto = {
        linha[0]
        for linha in conexao.execute(
            "SELECT identificador FROM protocolos WHERE texto_completo IS NOT NULL"
        ).fetchall()
    }

    async with Downloader(cache, delay_segundos=delay_segundos) as downloader:
        for item in itens:
            registro: dict[str, Any] = {
                "identificador": item.identificador,
                "condicao": item.condicao,
                "status": status.get(chave_nome(item.condicao)),
                "portaria": item.portaria,
                "data_portaria": item.data_portaria,
                "url_pdf": item.url_pdf,
                "url_resumido": item.url_resumido,
                "texto_completo": None,
                "secoes_json": None,
                "vigente": True,
                "substituido_por": None,
                "extracao_incompleta": False,
                "nota_atualizacao": item.nota_atualizacao,
            }

            precisa_texto = (
                item.url_pdf is not None
                and item.identificador not in ja_com_texto
                and (baixados < max_pdfs or cache.tem(item.url_pdf))
            )
            if precisa_texto and item.url_pdf:
                conteudo = await downloader.obter(item.url_pdf)
                if conteudo is None:
                    registro["extracao_incompleta"] = True
                else:
                    if not cache.tem(item.url_pdf):
                        baixados += 1
                    else:
                        baixados += 1
                    extraido = extrair(conteudo)
                    registro["texto_completo"] = extraido.texto or None
                    registro["secoes_json"] = (
                        json.dumps(extraido.secoes, ensure_ascii=False) if extraido.secoes else None
                    )
                    registro["extracao_incompleta"] = extraido.extracao_incompleta
            registros.append(registro)

    # Preserva texto já coletado antes: o upsert não pode apagá-lo com NULL.
    for registro in registros:
        if registro["texto_completo"] is None and registro["identificador"] in ja_com_texto:
            anterior = conexao.execute(
                "SELECT texto_completo, secoes_json FROM protocolos WHERE identificador = ?",
                [registro["identificador"]],
            ).fetchone()
            if anterior:
                registro["texto_completo"] = anterior[0]
                registro["secoes_json"] = anterior[1]

    novos, atualizados = gravar(conexao, registros)
    despromovidos = marcar_ausentes_como_substituidos(
        conexao, [r["identificador"] for r in registros]
    )
    logger.info(
        "PCDT: %d novos, %d atualizados, %d PDFs, %d fora da listagem",
        novos,
        atualizados,
        baixados,
        despromovidos,
    )
    return ResultadoColeta(
        novos=novos, atualizados=atualizados, pdfs_baixados=baixados, despromovidos=despromovidos
    )
