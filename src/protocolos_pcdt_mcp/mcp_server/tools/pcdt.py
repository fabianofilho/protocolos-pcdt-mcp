"""As duas tools: consultar protocolo e resumir conduta."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from protocolos_pcdt_mcp.llm.qwen_client import QwenClient, QwenIndisponivel
from protocolos_pcdt_mcp.llm.resumir import ResumoConduta, resumir
from protocolos_pcdt_mcp.store.db import BaseIndisponivel, conectar
from protocolos_pcdt_mcp.store.queries import buscar_condicao, buscar_no_texto, por_identificador

logger = logging.getLogger(__name__)

AVISO_BASE_VAZIA = (
    "A base local ainda não foi sincronizada com o portal da Conitec. "
    "Rode 'pcdt-cli sync' antes de consultar."
)
AVISO_BASE_TRAVADA = (
    "A base local existe mas não pôde ser lida agora, provavelmente há uma coleta em "
    "andamento. Tente de novo em alguns minutos."
)
AVISO_LEITURA = (
    "O resumo é uma ajuda de leitura gerada por LLM local, não substitui o protocolo. "
    "Confira a citação literal e abra o PDF completo antes de qualquer decisão."
)


class Protocolo(BaseModel):
    """Um PCDT, sempre com o link do documento oficial."""

    identificador: str
    condicao: str
    status: str | None = None
    portaria: str | None = None
    data_portaria: date | None = None
    nota_atualizacao: str | None = Field(
        default=None,
        description="Nota de revisão que o portal põe ao lado do nome, ex. 'Anexo alterado em ...'",
    )
    url_pdf: str | None = None
    url_resumido: str | None = None
    vigente: bool = True
    secoes_disponiveis: list[str] = Field(default_factory=list)
    texto_completo_disponivel: bool = False
    extracao_incompleta: bool = False


class RespostaConsulta(BaseModel):
    termo: str
    total: int
    resultados: list[Protocolo]
    aviso: str | None = None


class RespostaResumo(BaseModel):
    identificador: str
    condicao: str
    contexto_clinico: str
    resumo: ResumoConduta | None = None
    url_pdf: str | None = None
    aviso: str | None = None


def _para_modelo(linha: dict[str, Any]) -> Protocolo:
    secoes: list[str] = []
    if linha.get("secoes_json"):
        try:
            secoes = list(json.loads(linha["secoes_json"]).keys())
        except (json.JSONDecodeError, AttributeError):
            secoes = []
    return Protocolo(
        identificador=linha["identificador"],
        condicao=linha["condicao"],
        status=linha.get("status"),
        portaria=linha.get("portaria"),
        data_portaria=linha.get("data_portaria"),
        nota_atualizacao=linha.get("nota_atualizacao"),
        url_pdf=linha.get("url_pdf"),
        url_resumido=linha.get("url_resumido"),
        vigente=bool(linha.get("vigente", True)),
        secoes_disponiveis=secoes,
        texto_completo_disponivel=bool(linha.get("texto_completo")),
        extracao_incompleta=bool(linha.get("extracao_incompleta", False)),
    )


def _ler(caminho_db: str, funcao: Any) -> tuple[Any, str | None]:
    try:
        with conectar(caminho_db, somente_leitura=True) as conexao:
            return funcao(conexao), None
    except FileNotFoundError:
        return None, AVISO_BASE_VAZIA
    except BaseIndisponivel as erro:
        logger.warning("base indisponível: %s", erro)
        return None, AVISO_BASE_TRAVADA
    except Exception:  # noqa: BLE001 - nenhuma falha de base derruba a tool
        logger.exception("falha ao consultar a base")
        return None, AVISO_BASE_TRAVADA


async def consultar_protocolo(
    doenca_ou_condicao: str,
    *,
    caminho_db: str,
    limite: int = 10,
) -> RespostaConsulta:
    """PCDT vigente para uma doença ou condição.

    Busca primeiro pelo nome da condição; se não achar, procura no texto dos
    protocolos, porque uma condição pode ser tratada dentro do PCDT de outra.
    """
    termo = doenca_ou_condicao.strip()
    if not termo:
        return RespostaConsulta(termo=termo, total=0, resultados=[], aviso="Informe uma condição.")

    def consulta(conexao: Any) -> list[dict[str, Any]]:
        achados = buscar_condicao(conexao, termo, limite=limite)
        return achados if achados else buscar_no_texto(conexao, termo, limite=limite)

    linhas, aviso = _ler(caminho_db, consulta)
    if linhas is None:
        return RespostaConsulta(termo=termo, total=0, resultados=[], aviso=aviso)

    return RespostaConsulta(
        termo=termo,
        total=len(linhas),
        resultados=[_para_modelo(linha) for linha in linhas],
        aviso=None if linhas else "Nenhum PCDT encontrado para essa condição.",
    )


async def resumir_conduta(
    pcdt_id: str,
    contexto_clinico: str,
    *,
    caminho_db: str,
    qwen_endpoint: str,
    qwen_model: str,
    timeout_segundos: float = 180.0,
) -> RespostaResumo:
    """Resumo da conduta do protocolo, direcionado a um contexto clínico."""
    linha, aviso = _ler(caminho_db, lambda c: por_identificador(c, pcdt_id.strip().lower()))
    if aviso:
        return RespostaResumo(
            identificador=pcdt_id, condicao="", contexto_clinico=contexto_clinico, aviso=aviso
        )
    if linha is None:
        return RespostaResumo(
            identificador=pcdt_id,
            condicao="",
            contexto_clinico=contexto_clinico,
            aviso=(
                f"Nenhum protocolo com identificador {pcdt_id!r}. "
                "Use consultar_protocolo para achar o identificador correto."
            ),
        )

    texto = linha.get("texto_completo")
    if not texto:
        return RespostaResumo(
            identificador=linha["identificador"],
            condicao=linha["condicao"],
            contexto_clinico=contexto_clinico,
            url_pdf=linha.get("url_pdf"),
            aviso=(
                "O texto completo deste protocolo ainda não foi coletado. "
                "Abra o PDF pelo link, ou rode 'pcdt-cli sync' para baixá-lo."
            ),
        )

    try:
        async with QwenClient(
            qwen_endpoint, qwen_model, timeout_segundos=timeout_segundos
        ) as cliente:
            if not await cliente.esta_vivo():
                raise QwenIndisponivel("LLM local não respondeu")
            resultado = await resumir(
                cliente,
                condicao=linha["condicao"],
                texto_protocolo=texto,
                contexto_clinico=contexto_clinico,
            )
    except QwenIndisponivel as erro:
        return RespostaResumo(
            identificador=linha["identificador"],
            condicao=linha["condicao"],
            contexto_clinico=contexto_clinico,
            url_pdf=linha.get("url_pdf"),
            aviso=f"Não foi possível resumir: {erro}. O PDF continua acessível pelo link.",
        )

    if resultado.citacao_confere:
        complemento = ""
    elif resultado.fracao_citacao_verificada >= 0.5:
        complemento = (
            f" ATENÇÃO: a citação não aparece contígua no protocolo, embora "
            f"{int(resultado.fracao_citacao_verificada * 100)}% das frases existam nele, "
            "é uma costura de trechos de partes diferentes. Confira no PDF antes de citar."
        )
    else:
        complemento = (
            " ATENÇÃO: a citação NÃO foi encontrada no protocolo. Trate como não confiável "
            "e confira no PDF."
        )
    return RespostaResumo(
        identificador=linha["identificador"],
        condicao=linha["condicao"],
        contexto_clinico=contexto_clinico,
        resumo=resultado,
        url_pdf=linha.get("url_pdf"),
        aviso=AVISO_LEITURA + complemento,
    )
