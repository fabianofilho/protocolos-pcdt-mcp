"""Coleta: listagem, status, PDFs e extração."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from protocolos_pcdt_mcp.coleta.downloader import CachePdf, Downloader
from protocolos_pcdt_mcp.coleta.listagem import ItemPcdt, baixar_listagem, baixar_status
from protocolos_pcdt_mcp.extract.parser import extrair
from protocolos_pcdt_mcp.nomes import chave_nome
from protocolos_pcdt_mcp.store.queries import gravar, marcar_ausentes_como_substituidos

logger = logging.getLogger(__name__)

# Se a listagem trouxer menos que esta fração dos PCDTs vigentes na base, ela é
# tratada como parcial e ninguém é despromovido nesta execução.
FRACAO_MINIMA_LISTAGEM = 0.8


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

    # Documento de onde veio o texto já extraído. Se a URL, a data da portaria ou
    # a nota de revisão mudarem, o texto guardado é de outro documento.
    anteriores: dict[str, tuple[Any, ...]] = {
        linha[0]: tuple(linha[1:])
        for linha in conexao.execute(
            "SELECT identificador, url_pdf, data_portaria, nota_atualizacao "
            "FROM protocolos WHERE texto_completo IS NOT NULL"
        ).fetchall()
    }
    contagem_vigentes = conexao.execute("SELECT count(*) FROM protocolos WHERE vigente").fetchone()
    vigentes_antes = int(contagem_vigentes[0]) if contagem_vigentes else 0

    def mudou(item: ItemPcdt) -> bool:
        anterior = anteriores.get(item.identificador)
        return anterior is not None and anterior != documento(item)

    # Quem tem texto desatualizado vai primeiro, antes de quem nunca teve texto.
    fila = sorted(itens, key=lambda item: not mudou(item))
    desatualizados = sum(1 for item in itens if mudou(item))
    if desatualizados:
        logger.info("PCDT: %d protocolos mudaram de documento desde a extração", desatualizados)

    async with Downloader(cache, delay_segundos=delay_segundos) as downloader:
        for item in fila:
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

            documento_novo = mudou(item)
            if item.url_pdf is None or (item.identificador in anteriores and not documento_novo):
                registros.append(registro)
                continue

            # Mesma URL com documento novo: o cache guarda o PDF antigo. Ele sai
            # do cache já, antes da cota e do download. Se ficasse, e o texto
            # fosse descartado agora (sem cota, download falhou), o próximo sync
            # não veria mais o protocolo como "mudou" (sem texto ele sai de
            # anteriores) e reextrairia o PDF velho com a nota nova.
            if documento_novo and anteriores[item.identificador][0] == item.url_pdf:
                cache.remover(item.url_pdf)
            no_cache = cache.tem(item.url_pdf)
            if baixados >= max_pdfs and not no_cache:
                registros.append(registro)
                continue

            conteudo = await downloader.obter(item.url_pdf)
            if not no_cache:
                baixados += 1
            if conteudo is None:
                registro["extracao_incompleta"] = True
            else:
                extraido = extrair(conteudo)
                registro["texto_completo"] = extraido.texto or None
                registro["secoes_json"] = (
                    json.dumps(extraido.secoes, ensure_ascii=False) if extraido.secoes else None
                )
                registro["extracao_incompleta"] = extraido.extracao_incompleta
            registros.append(registro)

    # Preserva texto já coletado antes: o upsert não pode apagá-lo com NULL. Texto
    # de um documento que mudou não é preservado: é melhor dizer "ainda não
    # coletado" do que resumir a versão velha com o link da nova.
    for registro in registros:
        identificador = registro["identificador"]
        anterior = anteriores.get(identificador)
        if registro["texto_completo"] is None and anterior is not None:
            if anterior != (
                registro["url_pdf"],
                registro["data_portaria"],
                registro["nota_atualizacao"],
            ):
                continue
            linha = conexao.execute(
                "SELECT texto_completo, secoes_json FROM protocolos WHERE identificador = ?",
                [identificador],
            ).fetchone()
            if linha:
                registro["texto_completo"] = linha[0]
                registro["secoes_json"] = linha[1]

    novos, atualizados = gravar(conexao, registros)

    vistos = {r["identificador"] for r in registros}
    if vigentes_antes and len(vistos) < FRACAO_MINIMA_LISTAGEM * vigentes_antes:
        # Listagem parcial (paginação nova, parser pegando metade da tabela):
        # despromover agora tiraria de vigente, em silêncio, PCDTs que continuam
        # valendo. Melhor não despromover ninguém e deixar o aviso no journal.
        logger.warning(
            "PCDT: listagem trouxe %d protocolos contra %d vigentes na base; "
            "abaixo de %d%%, ninguém foi despromovido. Confira se o portal mudou.",
            len(vistos),
            vigentes_antes,
            int(FRACAO_MINIMA_LISTAGEM * 100),
        )
        despromovidos = 0
    else:
        despromovidos = marcar_ausentes_como_substituidos(conexao, sorted(vistos))
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


def documento(item: ItemPcdt) -> tuple[Any, ...]:
    """O que identifica o documento de onde o texto foi extraído."""
    return (item.url_pdf, item.data_portaria, item.nota_atualizacao)
