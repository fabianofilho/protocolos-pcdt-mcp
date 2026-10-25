"""As duas tools: consultar protocolo e resumir conduta."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from protocolos_pcdt_mcp.llm.qwen_client import QwenClient, QwenIndisponivel
from protocolos_pcdt_mcp.llm.resumir import ResumoConduta, resumir
from protocolos_pcdt_mcp.store.db import BaseIndisponivel, conectar
from protocolos_pcdt_mcp.store.queries import (
    buscar_condicao,
    buscar_no_texto,
    contar_condicao,
    contar_no_texto,
    por_identificador,
)

logger = logging.getLogger(__name__)

AVISO_BASE_VAZIA = (
    "A base local ainda não foi sincronizada com o portal da Conitec. "
    "Rode 'pcdt-cli sync' antes de consultar."
)
AVISO_BASE_TRAVADA = (
    "A base local existe mas não pôde ser lida agora, provavelmente há uma coleta em "
    "andamento. Tente de novo em alguns minutos."
)
AVISO_SO_NO_TEXTO = (
    "Nenhum PCDT com esse nome. Estes protocolos apenas citam o termo no texto e podem "
    "não ter relação com a condição; não trate nenhum deles como o PCDT dela."
)
AVISO_LEITURA = (
    "O resumo é uma ajuda de leitura gerada por LLM local, não substitui o protocolo. "
    "Confira a citação literal e abra o PDF completo antes de qualquer decisão."
)
AVISO_SEM_LLM_NO_CONNECTOR = (
    "Este servidor está em modo connector (HTTP público) e não gera resumos: não há "
    "LLM ligado a ele e não existe resumo guardado para este protocolo e contexto. "
    "Nenhum resumo foi produzido. Abra o PDF completo pelo link, ou rode o servidor "
    "localmente por stdio com um LLM configurado para obter o resumo com citação conferida."
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
    origem: Literal["nome", "texto"] = Field(
        default="nome",
        description=(
            "'nome': o termo casou com o nome do PCDT. 'texto': o nome não casou e o "
            "protocolo só cita o termo em algum ponto do texto"
        ),
    )


class RespostaConsulta(BaseModel):
    termo: str
    total: int = Field(
        description=(
            "Quantos protocolos casam com o termo na base inteira, não quantos vieram "
            "nesta resposta. Um termo comum aparece no texto de dezenas: 'diabetes' "
            "aparece em 47 dos 132."
        )
    )
    retornados: int = Field(
        default=0, description="Quantos vieram em 'resultados', no máximo 'limite'"
    )
    truncado: bool = Field(
        default=False,
        description=(
            "True quando total > retornados. Não conte os resultados para dizer quantos "
            "protocolos tratam do assunto: use 'total' e aumente 'limite' se precisar."
        ),
    )
    resultados: list[Protocolo]
    aviso: str | None = None


class RespostaResumo(BaseModel):
    identificador: str
    condicao: str
    contexto_clinico: str
    resumo: ResumoConduta | None = None
    url_pdf: str | None = None
    aviso: str | None = None


def _para_modelo(linha: dict[str, Any], origem: Literal["nome", "texto"] = "nome") -> Protocolo:
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
        origem=origem,
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
    limite_texto: int = 5,
) -> RespostaConsulta:
    """PCDT vigente para uma doença ou condição.

    Busca primeiro pelo nome da condição; se não achar, procura no texto dos
    protocolos, porque uma condição pode ser tratada dentro do PCDT de outra.
    Resultados do texto vêm marcados com ``origem="texto"`` e com um aviso, porque
    muitas vezes só mencionam o termo de passagem.
    """
    termo = doenca_ou_condicao.strip()
    if not termo:
        return RespostaConsulta(termo=termo, total=0, resultados=[], aviso="Informe uma condição.")

    def consulta(conexao: Any) -> tuple[list[dict[str, Any]], Literal["nome", "texto"], int]:
        achados = buscar_condicao(conexao, termo, limite=limite)
        if achados:
            return achados, "nome", contar_condicao(conexao, termo)
        return (
            buscar_no_texto(conexao, termo, limite=limite_texto),
            "texto",
            contar_no_texto(conexao, termo),
        )

    resultado, aviso = _ler(caminho_db, consulta)
    if resultado is None:
        return RespostaConsulta(termo=termo, total=0, resultados=[], aviso=aviso)

    linhas, origem, total = resultado
    truncado = total > len(linhas)
    if not linhas:
        aviso = "Nenhum PCDT encontrado para essa condição."
    elif origem == "texto":
        aviso = AVISO_SO_NO_TEXTO
    if truncado:
        aviso = (
            f"{aviso or ''} Casaram {total} protocolos e estão aqui os {len(linhas)} "
            f"primeiros. Não conte os resultados para dizer quantos tratam do assunto: "
            f"use 'total' e aumente 'limite' para ver mais."
        ).strip()
    return RespostaConsulta(
        termo=termo,
        total=total,
        retornados=len(linhas),
        truncado=truncado,
        resultados=[_para_modelo(linha, origem) for linha in linhas],
        aviso=aviso,
    )


async def resumir_conduta(
    pcdt_id: str,
    contexto_clinico: str,
    *,
    caminho_db: str,
    qwen_endpoint: str,
    qwen_model: str,
    timeout_segundos: float = 180.0,
    permitir_llm: bool = True,
) -> RespostaResumo:
    """Resumo da conduta do protocolo, direcionado a um contexto clínico.

    Com ``permitir_llm=False`` (modo connector) o LLM não é chamado. Não há cache
    de resumos, então a resposta traz o protocolo, o link do PDF e um aviso
    explícito de que nenhum resumo foi gerado, com ``resumo=None``.
    """
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

    if not permitir_llm:
        # Modo connector: nenhuma chamada ao LLM. Quem hospeda não paga
        # inferência por todo mundo, e nada é inventado no lugar do resumo.
        return RespostaResumo(
            identificador=linha["identificador"],
            condicao=linha["condicao"],
            contexto_clinico=contexto_clinico,
            url_pdf=linha.get("url_pdf"),
            aviso=AVISO_SEM_LLM_NO_CONNECTOR,
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
