"""Modo connector: limite de requisições, Host público, tetos e somente leitura."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from protocolos_pcdt_mcp.mcp_server.limite import LimitadorPorOrigem, origem_da_requisicao
from protocolos_pcdt_mcp.mcp_server.tools.pcdt import (
    AVISO_BASE_TRAVADA,
    AVISO_SEM_LLM_NO_CONNECTOR,
    consultar_protocolo,
    resumir_conduta,
)
from protocolos_pcdt_mcp.store.db import conectar
from protocolos_pcdt_mcp.store.queries import gravar

TEXTO = "3 TRATAMENTO. Primeira linha: fármaco A, 500 mg, duas vezes ao dia, por 30 dias."


def _registro(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identificador": "asma",
        "condicao": "Asma",
        "status": None,
        "portaria": None,
        "data_portaria": date(2026, 1, 1),
        "url_pdf": "https://www.gov.br/conitec/pcdt_asma.pdf",
        "url_resumido": None,
        "texto_completo": TEXTO,
        "secoes_json": json.dumps({"tratamento": TEXTO}),
        "vigente": True,
        "substituido_por": None,
        "extracao_incompleta": False,
    }
    base.update(campos)
    return base


# --- limite de requisições ----------------------------------------------


def test_limite_deixa_passar_ate_o_teto() -> None:
    limitador = LimitadorPorOrigem(3)
    assert [limitador.permitir("1.2.3.4") for _ in range(4)] == [True, True, True, False]


def test_limite_e_por_origem() -> None:
    """Um cliente em laço não pode consumir o limite dos outros."""
    limitador = LimitadorPorOrigem(2)
    assert limitador.permitir("1.1.1.1") and limitador.permitir("1.1.1.1")
    assert not limitador.permitir("1.1.1.1")
    assert limitador.permitir("2.2.2.2")


def test_origem_usa_forwarded_for_quando_ha_proxy() -> None:
    """Atrás de proxy, o IP do socket é o do proxy: todos viram a mesma origem."""
    scope = {
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 203.0.113.9")],
        "client": ("127.0.0.1", 5000),
    }
    # Vale a entrada que o proxy local acrescentou (a última), não a do cliente.
    assert origem_da_requisicao(scope) == "203.0.113.9"


def test_origem_ignora_forwarded_for_de_quem_nao_e_proxy() -> None:
    scope = {
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
        "client": ("198.51.100.7", 5000),
    }
    assert origem_da_requisicao(scope) == "198.51.100.7"


def test_tabela_cheia_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    from protocolos_pcdt_mcp.mcp_server import limite

    monkeypatch.setattr(limite, "MAX_ORIGENS", 2)
    limitador = LimitadorPorOrigem(100)
    assert limitador.permitir("a") and limitador.permitir("b")
    assert not limitador.permitir("c")
    assert limitador.motivo_ultima_recusa == "tabela"


def test_origem_cai_para_o_socket_sem_proxy() -> None:
    assert origem_da_requisicao({"headers": [], "client": ("198.51.100.7", 1234)}) == "198.51.100.7"


def test_origem_desconhecida_nao_quebra() -> None:
    assert origem_da_requisicao({}) == "desconhecido"


def test_teto_global_protege_independente_da_origem() -> None:
    """O teto que importa para tráfego vindo do Claude: ele chega de poucos IPs."""
    limitador = LimitadorPorOrigem(limite_por_minuto=100, limite_global_por_minuto=3)
    assert all(limitador.permitir(f"10.0.0.{i}") for i in range(3))
    assert not limitador.permitir("10.0.0.99")  # origem nova, mas o global estourou
    assert limitador.motivo_ultima_recusa == "global"


def test_motivo_distingue_os_dois_tetos() -> None:
    limitador = LimitadorPorOrigem(limite_por_minuto=1, limite_global_por_minuto=100)
    assert limitador.permitir("1.1.1.1")
    assert not limitador.permitir("1.1.1.1")
    assert limitador.motivo_ultima_recusa == "origem"


def test_sem_teto_global_so_vale_o_por_origem() -> None:
    limitador = LimitadorPorOrigem(limite_por_minuto=2, limite_global_por_minuto=0)
    assert all(limitador.permitir(f"10.0.0.{i}") for i in range(50))


async def test_middleware_responde_429_acima_do_teto() -> None:
    """O teto vale na borda HTTP, antes de a requisição chegar ao MCP."""
    from protocolos_pcdt_mcp.mcp_server.server import _com_limite

    chamadas: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        chamadas.append(scope["path"])

    enviados: list[dict[str, Any]] = []

    async def send(mensagem: dict[str, Any]) -> None:
        enviados.append(mensagem)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    embrulhado = _com_limite(app, limite_por_minuto=1, limite_global=10)
    scope = {"type": "http", "path": "/mcp", "headers": [], "client": ("198.51.100.7", 1)}
    await embrulhado(scope, receive, send)
    await embrulhado(scope, receive, send)

    assert chamadas == ["/mcp"]
    assert enviados[0]["status"] == 429


# --- configuração --------------------------------------------------------


def test_padrao_continua_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quem usa hoje por stdio não pode mudar de comportamento sem pedir."""
    from protocolos_pcdt_mcp.config import Config

    monkeypatch.delenv("TRANSPORTE", raising=False)
    config = Config(_env_file=None)  # type: ignore[call-arg]
    assert config.transporte == "stdio"
    assert config.modo_connector is False
    assert (config.http_host, config.http_path, config.http_stateless) == (
        "127.0.0.1",
        "/mcp",
        True,
    )


def test_streamable_http_liga_o_modo_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    from protocolos_pcdt_mcp.config import Config

    monkeypatch.setenv("TRANSPORTE", "streamable-http")
    assert Config(_env_file=None).modo_connector is True  # type: ignore[call-arg]


def test_hosts_publicos_aceita_lista_por_virgula(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem NoDecode o pydantic-settings tentaria ler o valor como JSON e estouraria."""
    from protocolos_pcdt_mcp.config import Config

    monkeypatch.setenv("HTTP_HOSTS_PUBLICOS", "a.exemplo.ts.net, b.exemplo.ts.net")
    config = Config(_env_file=None)  # type: ignore[call-arg]
    assert config.http_hosts_publicos == ["a.exemplo.ts.net", "b.exemplo.ts.net"]


# --- Host público ---------------------------------------------------------


def test_sem_host_publico_mantem_o_padrao_do_sdk() -> None:
    """Sem nome público declarado, quem decide é o SDK: só loopback."""
    from protocolos_pcdt_mcp.config import Config
    from protocolos_pcdt_mcp.mcp_server.server import _seguranca_de_transporte

    assert _seguranca_de_transporte(Config(http_hosts_publicos=[])) is None


def test_host_publico_entra_sem_derrubar_o_loopback() -> None:
    """Declarar o nome do túnel não pode cortar o acesso local, que é como se testa."""
    from protocolos_pcdt_mcp.config import Config
    from protocolos_pcdt_mcp.mcp_server.server import _seguranca_de_transporte

    regras = _seguranca_de_transporte(Config(http_hosts_publicos=["mcp.exemplo.ts.net"]))
    assert regras is not None
    assert regras.enable_dns_rebinding_protection is True
    assert "mcp.exemplo.ts.net" in regras.allowed_hosts
    assert "127.0.0.1:*" in regras.allowed_hosts
    assert "https://mcp.exemplo.ts.net" in regras.allowed_origins


def test_host_de_fora_da_lista_continua_recusado() -> None:
    """A proteção contra DNS rebinding continua valendo para quem não foi declarado."""
    from mcp.server.transport_security import TransportSecurityMiddleware

    from protocolos_pcdt_mcp.config import Config
    from protocolos_pcdt_mcp.mcp_server.server import _seguranca_de_transporte

    guarda = TransportSecurityMiddleware(
        _seguranca_de_transporte(Config(http_hosts_publicos=["mcp.exemplo.ts.net"]))
    )
    assert guarda._validate_host("mcp.exemplo.ts.net") is True
    assert guarda._validate_host("mcp.exemplo.ts.net:443") is True
    assert guarda._validate_host("site-do-atacante.exemplo") is False
    assert guarda._validate_host(None) is False


# --- tetos dos parâmetros -------------------------------------------------


async def test_consulta_tem_teto_de_limite_no_connector() -> None:
    """No connector público, 'limite' sem teto devolveria a base inteira."""
    from mcp.server.mcpserver.exceptions import ToolError

    from protocolos_pcdt_mcp.mcp_server.server import LIMITE_MAXIMO, mcp

    ferramentas = {t.name: t for t in await mcp.list_tools()}
    schema = ferramentas["consultar_protocolo"].input_schema["properties"]["limite"]
    assert (schema["minimum"], schema["maximum"]) == (1, LIMITE_MAXIMO)
    with pytest.raises(ToolError):
        await mcp.call_tool(
            "consultar_protocolo", {"doenca_ou_condicao": "asma", "limite": LIMITE_MAXIMO + 1}
        )


# --- sem LLM no modo connector ------------------------------------------


async def test_resumo_no_connector_nao_chama_o_llm(
    caminho_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem cache de resumos, a resposta diz que não há resumo, e não inventa um."""
    from protocolos_pcdt_mcp.mcp_server.tools import pcdt

    with conectar(caminho_db) as conexao:
        gravar(conexao, [_registro()])

    def proibido(*_: Any, **__: Any) -> None:
        raise AssertionError("o modo connector não pode chamar o LLM")

    monkeypatch.setattr(pcdt, "QwenClient", proibido)
    resposta = await resumir_conduta(
        "asma",
        "adulto sem comorbidade",
        caminho_db=caminho_db,
        qwen_endpoint="http://nao-deve-ser-chamado/v1",
        qwen_model="x",
        permitir_llm=False,
    )

    assert resposta.resumo is None
    assert resposta.aviso == AVISO_SEM_LLM_NO_CONNECTOR
    assert resposta.condicao == "Asma"
    assert resposta.url_pdf == "https://www.gov.br/conitec/pcdt_asma.pdf"


async def test_tool_do_servidor_segue_o_transporte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool registrada lê TRANSPORTE: em streamable-http o LLM fica desligado."""
    from protocolos_pcdt_mcp.mcp_server import server
    from protocolos_pcdt_mcp.mcp_server.tools import pcdt

    caminho = tmp_path / "pcdt.duckdb"
    with conectar(caminho) as conexao:
        gravar(conexao, [_registro()])

    def proibido(*_: Any, **__: Any) -> None:
        raise AssertionError("o modo connector não pode chamar o LLM")

    monkeypatch.setattr(pcdt, "QwenClient", proibido)
    monkeypatch.setenv("TRANSPORTE", "streamable-http")
    monkeypatch.setenv("DUCKDB_PATH", str(caminho))

    resposta = await server.resumir_conduta("asma", "gestante")
    assert resposta.resumo is None
    assert resposta.aviso == AVISO_SEM_LLM_NO_CONNECTOR


# --- o servidor só lê, e a coleta escreve no mesmo arquivo -------------------


def test_escritor_e_bloqueado_por_leitor(tmp_path: Path) -> None:
    """Por isso o servidor nunca escreve: o DuckDB recusa escritor com leitor aberto."""
    servida = tmp_path / "pcdt.duckdb"
    with conectar(servida) as conexao:
        gravar(conexao, [_registro()])

    leitor = duckdb.connect(str(servida), read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            duckdb.connect(str(servida))
    finally:
        leitor.close()


async def test_consulta_durante_coleta_avisa_em_vez_de_quebrar(tmp_path: Path) -> None:
    """Coleta em curso, em outro processo, segura a base: a tool devolve aviso, não erro."""
    servida = tmp_path / "pcdt.duckdb"
    with conectar(servida) as conexao:
        gravar(conexao, [_registro()])

    escritor = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); "
            "print('aberto', flush=True); time.sleep(30)",
            str(servida),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert escritor.stdout is not None
        assert escritor.stdout.readline().strip() == "aberto"
        resposta = await consultar_protocolo("asma", caminho_db=str(servida))
    finally:
        escritor.kill()
        escritor.wait()

    assert resposta.total == 0
    assert resposta.aviso == AVISO_BASE_TRAVADA
