"""Entrypoint MCP (stdio) dos PCDTs."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from protocolos_pcdt_mcp.config import carregar_config
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import RespostaConsulta, RespostaResumo
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import consultar_protocolo as _consultar_protocolo
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import resumir_conduta as _resumir_conduta

logger = logging.getLogger(__name__)

mcp = MCPServer("protocolos-pcdt-mcp", version="0.1.0")


@mcp.tool()
async def consultar_protocolo(doenca_ou_condicao: str) -> RespostaConsulta:
    """Consulta o PCDT vigente do Ministério da Saúde para uma doença ou condição.

    Devolve nome da condição, status, portaria, link do PDF completo e do PCDT
    resumido, e quais seções foram extraídas. Se houver mais de um protocolo
    relacionado, devolve todos — a escolha é de quem pergunta.

    Args:
        doenca_ou_condicao: nome da doença ou condição, por exemplo "asma".
    """
    config = carregar_config()
    return await _consultar_protocolo(doenca_ou_condicao, caminho_db=str(config.duckdb_path))


@mcp.tool()
async def resumir_conduta(pcdt_id: str, contexto_clinico: str) -> RespostaResumo:
    """Resume a conduta de um PCDT para um contexto clínico específico.

    Em vez de devolver o protocolo inteiro, extrai a parte que responde ao
    contexto — por exemplo "paciente com contraindicação a metformina". A
    resposta traz sempre a citação literal do trecho que sustenta o resumo, e
    diz se essa citação foi de fato encontrada no protocolo.

    É ajuda de leitura, não substitui o protocolo: o link do PDF vem junto.

    Args:
        pcdt_id: identificador devolvido por consultar_protocolo.
        contexto_clinico: a situação concreta sobre a qual se quer a conduta.
    """
    config = carregar_config()
    return await _resumir_conduta(
        pcdt_id,
        contexto_clinico,
        caminho_db=str(config.duckdb_path),
        qwen_endpoint=config.qwen_endpoint,
        qwen_model=config.qwen_model,
        timeout_segundos=config.qwen_timeout_segundos,
    )


def main() -> None:
    """Sobe o servidor MCP no stdio."""
    config = carregar_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("protocolos-pcdt-mcp subindo (base em %s)", config.duckdb_path)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
