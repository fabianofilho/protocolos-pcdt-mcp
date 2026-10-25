"""Entrypoint MCP (stdio) dos PCDTs."""

from __future__ import annotations

import base64
import logging
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import Icon

from protocolos_pcdt_mcp.config import carregar_config
from protocolos_pcdt_mcp.mcp_server.capacidades import esconder_o_que_nao_existe
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import RespostaConsulta, RespostaResumo
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import consultar_protocolo as _consultar_protocolo
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import resumir_conduta as _resumir_conduta

logger = logging.getLogger(__name__)

# Ícone do servidor e das tools, no molde da família (ecg-mcp): o robô do IA.med
# com prancheta com check no visor. Em data URI, e não URL: o cliente precisa ver o
# ícone antes de autenticar, e o endereço do Funnel é um só para todos os servidores.
ICONE = [
    Icon(
        src="data:image/svg+xml;base64,"
        + base64.b64encode((Path(__file__).parent / "icone.svg").read_bytes()).decode(),
        mimeType="image/svg+xml",
        sizes=["any"],
    )
]

mcp = MCPServer("protocolos-pcdt-mcp", version="0.1.0", icons=ICONE)


@mcp.tool(icons=ICONE)
async def consultar_protocolo(doenca_ou_condicao: str, limite: int = 10) -> RespostaConsulta:
    """Consulta o PCDT vigente do Ministério da Saúde para uma doença ou condição.

    Procura primeiro no nome dos PCDTs, sem acento, com as palavras em qualquer
    ordem e aceitando algumas siglas ("HAS", "DPOC", "DM2") e a grafia "diabetes
    mellitus". Se o nome não casar, procura o termo no texto dos protocolos e
    devolve até 5, com origem="texto" e um aviso: esses só citam o termo e
    muitas vezes não são o PCDT da condição.

    A resposta traz `total` (quantos casam na base) e `retornados` (quantos vieram).
    Com `truncado=true`, não conte os resultados para dizer quantos protocolos
    tratam do assunto: um termo comum aparece no texto de dezenas deles.

    Cada resultado traz identificador (use em resumir_conduta), nome da
    condição, portaria, link do PDF completo e do PCDT resumido, seções extraídas
    e se o texto completo já foi coletado. O status vem do CSV de dados abertos
    e só existe para parte dos PCDTs; null não quer dizer que o protocolo não
    está aprovado. Se houver mais de um protocolo pelo nome, devolve todos (até
    10), a escolha é de quem pergunta.

    Args:
        doenca_ou_condicao: nome da doença ou condição, por exemplo "asma".
    """
    config = carregar_config()
    return await _consultar_protocolo(
        doenca_ou_condicao, caminho_db=str(config.duckdb_path), limite=limite
    )


@mcp.tool(icons=ICONE)
async def resumir_conduta(pcdt_id: str, contexto_clinico: str) -> RespostaResumo:
    """Resume a conduta de um PCDT para um contexto clínico específico.

    Em vez de devolver o protocolo inteiro, extrai a parte que responde ao
    contexto, por exemplo "paciente com contraindicação a metformina". A
    resposta traz sempre a citação literal do trecho que sustenta o resumo, e
    diz se essa citação foi de fato encontrada no protocolo.

    É ajuda de leitura, não substitui o protocolo: o link do PDF vem junto.

    Precisa de um LLM local configurado e do texto completo do protocolo já
    coletado; sem isso, devolve só o link do PDF e um aviso.

    Args:
        pcdt_id: identificador devolvido por consultar_protocolo. O nome da
            condição sem a nota de revisão também é aceito, por exemplo "asma".
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
    # Este servidor so tem tools. Anunciar prompts e resources faria quem
    # mapeia o servidor gastar chamadas para descobrir lista vazia.
    esconder_o_que_nao_existe(mcp)
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
