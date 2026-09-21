"""PDF do protocolo para texto, segmentado por seção quando dá.

A estrutura dos PCDTs varia bastante entre protocolos antigos e novos, então a
segmentação degrada para texto corrido quando os títulos não são reconhecíveis,
guardar o protocolo inteiro sem seções é melhor do que descartá-lo.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_ESPACOS = re.compile(r"[ \t]+")
_LINHAS_VAZIAS = re.compile(r"\n{3,}")

# Títulos que aparecem na maioria dos PCDTs, com ou sem numeração.
_SECOES_CONHECIDAS = (
    ("introducao", r"INTRODU[ÇC][ÃA]O"),
    ("classificacao", r"CLASSIFICA[ÇC][ÃA]O ESTATÍSTICA|CLASSIFICA[ÇC][ÃA]O INTERNACIONAL|CID"),
    ("diagnostico", r"DIAGN[ÓO]STICO|CRIT[ÉE]RIOS DE INCLUS[ÃA]O"),
    ("tratamento", r"TRATAMENTO|F[ÁA]RMACOS|ESQUEMAS? DE ADMINISTRA[ÇC][ÃA]O"),
    ("monitoramento", r"MONITORIZA[ÇC][ÃA]O|MONITORAMENTO|ACOMPANHAMENTO"),
)


@dataclass
class ProtocoloExtraido:
    """Texto do protocolo e suas seções."""

    texto: str
    paginas: int
    secoes: dict[str, str] = field(default_factory=dict)
    extracao_incompleta: bool = False
    motivo: str | None = None


def limpar(bruto: str) -> str:
    texto = bruto.replace("\r\n", "\n").replace("\xa0", " ")
    texto = _ESPACOS.sub(" ", texto)
    return _LINHAS_VAZIAS.sub("\n\n", texto).strip()


def segmentar(texto: str) -> dict[str, str]:
    """Separa o texto pelas seções conhecidas.

    Devolve só as seções que foram realmente encontradas. Dicionário vazio
    significa que o protocolo não seguiu um formato reconhecível, o texto
    corrido continua disponível.
    """
    marcas: list[tuple[int, str]] = []
    for chave, padrao in _SECOES_CONHECIDAS:
        achado = re.search(rf"^\s*(?:\d+\.?\s*)?{padrao}\b.*$", texto, re.MULTILINE | re.IGNORECASE)
        if achado:
            marcas.append((achado.start(), chave))

    if len(marcas) < 2:
        return {}

    marcas.sort()
    secoes: dict[str, str] = {}
    for indice, (inicio, chave) in enumerate(marcas):
        fim = marcas[indice + 1][0] if indice + 1 < len(marcas) else len(texto)
        trecho = texto[inicio:fim].strip()
        if trecho:
            secoes[chave] = trecho
    return secoes


def extrair(conteudo: bytes) -> ProtocoloExtraido:
    """Texto completo e seções de um PDF de protocolo."""
    if not conteudo:
        return ProtocoloExtraido(texto="", paginas=0, extracao_incompleta=True, motivo="PDF vazio")

    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            paginas = [pagina.extract_text() or "" for pagina in pdf.pages]
    except Exception as erro:  # noqa: BLE001 - PDF ruim não pode parar a coleta
        logger.warning("extração falhou: %s", type(erro).__name__)
        return ProtocoloExtraido(
            texto="",
            paginas=0,
            extracao_incompleta=True,
            motivo=f"extração falhou: {type(erro).__name__}",
        )

    texto = limpar("\n".join(paginas))
    if not texto:
        return ProtocoloExtraido(
            texto="",
            paginas=len(paginas),
            extracao_incompleta=True,
            motivo="PDF sem camada de texto (provavelmente digitalizado)",
        )
    return ProtocoloExtraido(texto=texto, paginas=len(paginas), secoes=segmentar(texto))
