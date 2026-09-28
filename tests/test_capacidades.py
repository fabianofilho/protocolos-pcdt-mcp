"""O handshake nao pode prometer o que o servidor nao tem."""

from __future__ import annotations


def test_nao_anuncia_prompts_nem_resources_sem_ter_nenhum() -> None:
    """Handshake prometendo lista vazia custa chamada e sugere recurso inexistente."""
    from mcp.server.mcpserver import MCPServer

    from protocolos_pcdt_mcp.mcp_server.capacidades import esconder_o_que_nao_existe

    servidor = MCPServer("teste", version="0.1")

    @servidor.tool()
    async def exemplo(x: str) -> str:
        """Tool qualquer."""
        return x

    esconder_o_que_nao_existe(servidor)
    capacidades = servidor._lowlevel_server.get_capabilities()

    assert capacidades.prompts is None
    assert capacidades.resources is None
    assert capacidades.tools is not None, "tools continua anunciado"


def test_recurso_cadastrado_continua_anunciado() -> None:
    """A supressão não pode esconder o que de fato existe."""
    from mcp.server.mcpserver import MCPServer

    from protocolos_pcdt_mcp.mcp_server.capacidades import esconder_o_que_nao_existe

    servidor = MCPServer("teste", version="0.1")

    @servidor.resource("config://exemplo")
    def recurso() -> str:
        """Um recurso de verdade."""
        return "conteudo"

    esconder_o_que_nao_existe(servidor)
    capacidades = servidor._lowlevel_server.get_capabilities()

    assert capacidades.resources is not None, "existe recurso, tem que ser anunciado"
    assert capacidades.prompts is None


def test_sdk_diferente_nao_derruba_o_servidor() -> None:
    """Isto mexe em estrutura interna do SDK: mudança lá não pode virar exceção aqui."""
    from protocolos_pcdt_mcp.mcp_server.capacidades import esconder_o_que_nao_existe

    class Estranho:
        pass

    assert esconder_o_que_nao_existe(Estranho()) == ()
