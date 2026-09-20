"""CLI de administração: coleta manual e teste das tools."""

from __future__ import annotations

import asyncio
import json
import logging

import typer

from protocolos_pcdt_mcp.coleta.sync import coletar
from protocolos_pcdt_mcp.config import carregar_config
from protocolos_pcdt_mcp.llm.qwen_client import QwenClient
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import consultar_protocolo, resumir_conduta
from protocolos_pcdt_mcp.store.db import conectar, reindexar_fts

app = typer.Typer(help="Administração do protocolos-pcdt-mcp", no_args_is_help=True)


@app.command()
def sync(max_pdfs: int = typer.Option(20, help="Teto de PDFs baixados nesta execução")) -> None:
    """Atualiza a base a partir do portal da Conitec."""
    config = carregar_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    async def rodar() -> None:
        with conectar(config.duckdb_path) as conexao:
            resultado = await coletar(
                conexao,
                delay_segundos=config.coleta_delay_segundos,
                max_pdfs=max_pdfs,
                diretorio_cache=config.duckdb_path.parent / "pdfs",
            )
            indexado = reindexar_fts(conexao)
            typer.echo(
                f"{resultado.novos} novos, {resultado.atualizados} atualizados, "
                f"{resultado.pdfs_baixados} PDFs, {resultado.despromovidos} fora da listagem"
            )
            typer.echo(f"índice FTS: {'criado' if indexado else 'indisponível (busca por LIKE)'}")

    asyncio.run(rodar())


@app.command()
def consultar(condicao: str) -> None:
    """Testa a consulta fora do MCP."""
    config = carregar_config()
    resposta = asyncio.run(consultar_protocolo(condicao, caminho_db=str(config.duckdb_path)))
    typer.echo(resposta.model_dump_json(indent=2))


@app.command()
def resumir(pcdt_id: str, contexto: str) -> None:
    """Testa o resumo direcionado fora do MCP."""
    config = carregar_config()
    resposta = asyncio.run(
        resumir_conduta(
            pcdt_id,
            contexto,
            caminho_db=str(config.duckdb_path),
            qwen_endpoint=config.qwen_endpoint,
            qwen_model=config.qwen_model,
            timeout_segundos=config.qwen_timeout_segundos,
        )
    )
    typer.echo(resposta.model_dump_json(indent=2))


@app.command()
def llm() -> None:
    """Verifica se o LLM local responde."""
    config = carregar_config()

    async def checar() -> None:
        async with QwenClient(config.qwen_endpoint, config.qwen_model) as cliente:
            vivo = await cliente.esta_vivo()
        cor = typer.colors.GREEN if vivo else typer.colors.RED
        typer.secho(f"LLM local {'respondendo' if vivo else 'fora do ar'}", fg=cor)

    asyncio.run(checar())


@app.command()
def schema() -> None:
    """Contagens da base local."""
    config = carregar_config()
    with conectar(config.duckdb_path) as conexao:
        total = conexao.execute("SELECT count(*) FROM protocolos").fetchone()
        com_texto = conexao.execute(
            "SELECT count(*) FROM protocolos WHERE texto_completo IS NOT NULL"
        ).fetchone()
        vigentes = conexao.execute("SELECT count(*) FROM protocolos WHERE vigente").fetchone()
    typer.echo(
        json.dumps(
            {
                "protocolos": int(total[0]) if total else 0,
                "vigentes": int(vigentes[0]) if vigentes else 0,
                "com_texto_completo": int(com_texto[0]) if com_texto else 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
