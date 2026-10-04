"""Entrypoint MCP dos PCDTs: stdio por padrão, HTTP no modo connector."""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from protocolos_pcdt_mcp.config import carregar_config
from protocolos_pcdt_mcp.mcp_server.capacidades import esconder_o_que_nao_existe
from protocolos_pcdt_mcp.mcp_server.limite import LimitadorPorOrigem, origem_da_requisicao
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import RespostaConsulta, RespostaResumo
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import consultar_protocolo as _consultar_protocolo
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import resumir_conduta as _resumir_conduta

logger = logging.getLogger(__name__)

mcp = MCPServer("protocolos-pcdt-mcp", version="0.1.0")

# Teto do parâmetro limite. O connector é público: sem teto, uma chamada com
# limite enorme devolve a base inteira e um cliente em laço multiplica isso.
LIMITE_MAXIMO = 100


@mcp.tool()
async def consultar_protocolo(
    doenca_ou_condicao: str,
    limite: Annotated[int, Field(ge=1, le=LIMITE_MAXIMO)] = 10,
) -> RespostaConsulta:
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
        limite: quantos protocolos trazer pelo nome, de 1 a 100.
    """
    config = carregar_config()
    return await _consultar_protocolo(
        doenca_ou_condicao, caminho_db=str(config.duckdb_path), limite=limite
    )


@mcp.tool()
async def resumir_conduta(pcdt_id: str, contexto_clinico: str) -> RespostaResumo:
    """Resume a conduta de um PCDT para um contexto clínico específico.

    Em vez de devolver o protocolo inteiro, extrai a parte que responde ao
    contexto, por exemplo "paciente com contraindicação a metformina". A
    resposta traz sempre a citação literal do trecho que sustenta o resumo, e
    diz se essa citação foi de fato encontrada no protocolo.

    É ajuda de leitura, não substitui o protocolo: o link do PDF vem junto.

    Precisa de um LLM local configurado e do texto completo do protocolo já
    coletado; sem isso, devolve só o link do PDF e um aviso.

    No modo connector (servidor HTTP público) o LLM nunca é chamado: a resposta
    vem com `resumo=null`, o link do PDF e um aviso dizendo que nenhum resumo
    foi gerado. Nesse caso não afirme nada sobre a conduta sem abrir o PDF.

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
        # No modo connector o servidor não chama o LLM.
        permitir_llm=not config.modo_connector,
    )


def _seguranca_de_transporte(config: Any) -> Any:
    """Regras de Host/Origin quando o servidor atende por um nome publico.

    O SDK so liga a protecao contra DNS rebinding sozinho quando o bind e
    loopback, e ai aceita apenas Host de loopback. Atras de um tunel ou proxy o
    Host que chega e o nome publico, e a requisicao legitima levaria 421. Em vez
    de desligar a checagem, declaramos os nomes por onde o servidor responde.

    Devolve None quando nao ha nome publico configurado, deixando o padrao do
    SDK valer.
    """
    if not config.http_hosts_publicos:
        return None

    from mcp.server.transport_security import TransportSecuritySettings

    hosts: list[str] = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origens: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]
    for nome in config.http_hosts_publicos:
        # Com e sem porta: atras de TLS o Host costuma vir sem o ":443".
        hosts += [nome, f"{nome}:*"]
        origens += [f"https://{nome}", f"https://{nome}:*"]
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origens)


def _com_limite(app: Any, limite_por_minuto: int, limite_global: int) -> Any:
    """Embrulha o app ASGI com o teto de requisicoes por origem.

    O `run()` do SDK nao aceita middleware, entao o app e construido por
    `streamable_http_app()`, embrulhado aqui e servido por uvicorn.
    """
    limitador = LimitadorPorOrigem(limite_por_minuto, limite_global)

    async def middleware(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return
        origem = origem_da_requisicao(scope)
        if not limitador.permitir(origem):
            logger.warning("limite %s excedido (origem %s)", limitador.motivo_ultima_recusa, origem)
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"60"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"erro":"limite de requisicoes excedido; tente em 1 minuto"}',
                }
            )
            return
        await app(scope, receive, send)

    return middleware


def main() -> None:
    """Sobe o servidor MCP. Stdio por padrao; HTTP no modo connector."""
    config = carregar_config()
    # Este servidor so tem tools. Anunciar prompts e resources faria quem mapeia
    # o servidor gastar chamadas para descobrir lista vazia.
    esconder_o_que_nao_existe(mcp)
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if config.transporte == "stdio":
        logger.info("protocolos-pcdt-mcp subindo em stdio (base em %s)", config.duckdb_path)
        mcp.run(transport="stdio")
        return

    # Modo connector. A base é aberta somente para leitura a cada requisição, o
    # LLM nunca é chamado e o sync roda FORA deste processo. Hoje o sync grava
    # direto no arquivo servido: enquanto ele roda, as consultas recebem o aviso
    # de base travada (ver README, modo connector).
    import uvicorn

    app = _com_limite(
        mcp.streamable_http_app(
            streamable_http_path=config.http_path,
            stateless_http=config.http_stateless,
            host=config.http_host,
            transport_security=_seguranca_de_transporte(config),
        ),
        config.http_limite_por_minuto,
        config.http_limite_global_por_minuto,
    )
    logger.info(
        "protocolos-pcdt-mcp em http://%s:%d%s (stateless=%s, limite %d/min por origem e "
        "%d/min global, base=%s)",
        config.http_host,
        config.http_porta,
        config.http_path,
        config.http_stateless,
        config.http_limite_por_minuto,
        config.http_limite_global_por_minuto,
        config.duckdb_path,
    )
    uvicorn.run(app, host=config.http_host, port=config.http_porta, log_level="warning")


if __name__ == "__main__":
    main()
