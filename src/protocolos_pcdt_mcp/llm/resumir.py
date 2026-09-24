"""Resumo de conduta direcionado a um contexto clínico.

O protocolo inteiro pode ter centenas de páginas: esta camada devolve a parte que
responde à pergunta, **sempre com a citação literal** do trecho que a sustenta,
para o leitor conferir sem sair do lugar.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from importlib.resources import files

from jinja2 import Environment, Template, select_autoescape
from pydantic import BaseModel, Field

from protocolos_pcdt_mcp.llm.qwen_client import QwenClient

logger = logging.getLogger(__name__)

NOME_TEMPLATE = "resumir_conduta.jinja2"

# Quanto do protocolo cabe no contexto do modelo com folga para a resposta.
MAX_CARACTERES_PROTOCOLO = 24_000


class ResumoConduta(BaseModel):
    """Resumo direcionado, com a citação que o sustenta."""

    resumo: str
    secao_origem: str | None = None
    citacao_literal: str | None = Field(
        default=None,
        description="Trecho literal do protocolo; None quando o protocolo não trata do contexto",
    )
    citacao_confere: bool = Field(
        default=False,
        description="True quando a citação inteira foi encontrada literal e contígua no protocolo",
    )
    fracao_citacao_verificada: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Quanto da citação existe no protocolo, frase a frase. Um valor alto com "
            "citacao_confere=False indica costura de trechos de partes diferentes"
        ),
    )


@lru_cache(maxsize=1)
def _template() -> Template:
    """O prompt mora dentro do pacote, para ir junto no wheel."""
    fonte = files("protocolos_pcdt_mcp").joinpath("prompts", NOME_TEMPLATE).read_text("utf-8")
    ambiente = Environment(autoescape=select_autoescape(default=False, default_for_string=False))
    return ambiente.from_string(fonte)


def renderizar_prompt() -> str:
    return _template().render()


# Marcadores de omissão que o modelo às vezes põe na citação: "[...]", "(...)",
# reticências em três pontos ou no caractere único U+2026.
_ELIPSE = re.compile(r"\[\s*(?:\.{3}|\u2026)\s*\]|\(\s*(?:\.{3}|\u2026)\s*\)|\.{3,}|\u2026")


def _normalizar(texto: str) -> str:
    return " ".join(texto.split()).lower()


def _sem_elipse_nas_pontas(citacao: str) -> str:
    """Tira marcadores de omissão do começo e do fim, que só indicam recorte."""
    texto = citacao.strip()
    while True:
        antes = texto
        inicio = _ELIPSE.match(texto)
        if inicio:
            texto = texto[inicio.end() :].strip()
        fim = next((m for m in _ELIPSE.finditer(texto) if m.end() == len(texto)), None)
        if fim:
            texto = texto[: fim.start()].strip()
        if texto == antes:
            return texto


def conferir_citacao(citacao: str | None, texto_protocolo: str) -> bool:
    """A citação literal existe mesmo no protocolo, inteira e contígua?

    Um modelo pequeno às vezes 'cita' parafraseando, e às vezes costura frases de
    partes diferentes do documento num único bloco de aspas. Conferir é barato e
    transforma um campo de confiança em um fato verificável.

    Marcadores de omissão nas pontas ("[...]", "...") são ignorados, porque só
    dizem que o trecho foi recortado. Um marcador no meio significa que algo foi
    pulado, e aí a citação já não é contígua: fica para ``fracao_verificada``.
    """
    if not citacao or not citacao.strip():
        return False
    limpa = _sem_elipse_nas_pontas(citacao)
    if not limpa or _ELIPSE.search(limpa):
        return False
    return _normalizar(limpa) in _normalizar(texto_protocolo)


def fracao_verificada(citacao: str | None, texto_protocolo: str) -> float:
    """Quanto da citação existe no protocolo, frase a frase.

    Serve para distinguir dois casos que um booleano mistura: uma citação
    inventada do zero (fração perto de 0) e uma costura de trechos reais tirados
    de partes diferentes (fração alta, mas não contígua). A segunda é mais
    comum e mais traiçoeira, porque parece legítima na leitura.

    Marcadores de omissão ("[...]", "(...)", "...") separam pedaços e são
    descartados: não fazem parte do texto do protocolo.
    """
    if not citacao or not citacao.strip():
        return 0.0
    alvo = _normalizar(texto_protocolo)
    pedacos = [p for p in _ELIPSE.split(citacao) if p.strip()]
    frases = [
        f.strip()
        for pedaco in pedacos
        for f in re.split(r"(?<=[.;:])\s+", pedaco)
        if len(f.strip()) > 20
    ]
    if not frases:
        juntos = " ".join(pedacos)
        return 1.0 if juntos.strip() and _normalizar(juntos) in alvo else 0.0
    encontradas = sum(1 for frase in frases if _normalizar(frase) in alvo)
    return round(encontradas / len(frases), 2)


def _recortar(texto: str, contexto: str) -> str:
    """Pedaço do protocolo mais provável de conter a resposta.

    Protocolos passam de 24 mil caracteres com frequência; mandar o texto inteiro
    estoura o contexto. O recorte prioriza a vizinhança das palavras do contexto
    clínico perguntado.
    """
    if len(texto) <= MAX_CARACTERES_PROTOCOLO:
        return texto

    alvo = texto.lower()
    termos = [p for p in contexto.lower().split() if len(p) > 4]
    posicao = next((alvo.find(t) for t in termos if alvo.find(t) >= 0), -1)
    if posicao < 0:
        return texto[:MAX_CARACTERES_PROTOCOLO]

    metade = MAX_CARACTERES_PROTOCOLO // 2
    inicio = max(0, posicao - metade)
    return texto[inicio : inicio + MAX_CARACTERES_PROTOCOLO]


async def resumir(
    cliente: QwenClient,
    *,
    condicao: str,
    texto_protocolo: str,
    contexto_clinico: str,
) -> ResumoConduta:
    """Resumo da conduta do protocolo para o contexto dado."""
    recorte = _recortar(texto_protocolo, contexto_clinico)
    usuario = (
        f"Condição: {condicao}\n"
        f"Contexto clínico: {contexto_clinico}\n\n"
        f"Trecho do protocolo:\n{recorte}"
    )
    bruto = await cliente.pedir_json(renderizar_prompt(), usuario)

    citacao = bruto.get("citacao_literal")
    citacao_texto = str(citacao).strip() if citacao else None
    return ResumoConduta(
        resumo=str(bruto.get("resumo", "")).strip(),
        secao_origem=(str(bruto["secao_origem"]).strip() if bruto.get("secao_origem") else None),
        citacao_literal=citacao_texto,
        citacao_confere=conferir_citacao(citacao_texto, texto_protocolo),
        fracao_citacao_verificada=fracao_verificada(citacao_texto, texto_protocolo),
    )
