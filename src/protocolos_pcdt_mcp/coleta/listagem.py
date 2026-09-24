"""Listagem dos PCDTs vigentes.

Duas fontes, confirmadas em 20/09/2026 abrindo as páginas:

1. A tabela de PCDTs do portal da Conitec (gov.br): nome da condição, portaria
   com data, link do PDF completo e link do PCDT resumido. É a única que traz os
   PDFs.
2. O CSV de dados abertos do Ministério da Saúde, nome, status e tipo de cada
   PCDT. Serve para saber a situação (aprovado, em elaboração na Conitec) sem
   depender de interpretar a tabela HTML.

Nenhuma URL aqui foi deduzida: as duas foram baixadas e conferidas.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from protocolos_pcdt_mcp.nomes import chave_nome, identificador_de, separar_nota

logger = logging.getLogger(__name__)

BASE_GOVBR = "https://www.gov.br"
URL_LISTAGEM = (
    "https://www.gov.br/conitec/pt-br/assuntos/avaliacao-de-tecnologias-em-saude/"
    "protocolos-clinicos-e-diretrizes-terapeuticas/pcdt"
)
URL_STATUS_CSV = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CONITEC/csv/pcdt.csv.zip"

# Identifica o projeto para quem administra o portal, com link para o repositorio.
# Um coletor publico anonimo e ma cidadania: se algo incomodar do outro lado,
# precisa haver como descobrir o que e e falar com quem mantem.
USER_AGENT = "protocolos-pcdt-mcp/0.1 (+https://github.com/fabianofilho/protocolos-pcdt-mcp)"

_DATA_NA_PORTARIA = re.compile(r"(\d{2}/\d{2}/\d{4})")


class ListagemIndisponivel(RuntimeError):
    """A listagem respondeu, mas não no formato esperado."""


@dataclass(frozen=True)
class ItemPcdt:
    """Uma linha da tabela de PCDTs."""

    condicao: str
    url_pdf: str | None
    url_resumido: str | None
    portaria: str | None
    data_portaria: date | None
    # Nota de revisão que o portal pendura no nome ("Anexo alterado em ...").
    nota_atualizacao: str | None = None

    @property
    def identificador(self) -> str:
        """Chave estável: a condição normalizada, sem nota de revisão."""
        return identificador_de(self.condicao)


def _absoluta(href: object) -> str:
    """O bs4 tipa href como str ou lista; na prática é sempre str aqui."""
    texto = href if isinstance(href, str) else str(href)
    return texto if texto.startswith("http") else urljoin(BASE_GOVBR, texto)


def _data(texto: str) -> date | None:
    achado = _DATA_NA_PORTARIA.search(texto or "")
    if not achado:
        return None
    try:
        return datetime.strptime(achado.group(1), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_listagem(html: str) -> list[ItemPcdt]:
    """Extrai os PCDTs da tabela do portal.

    A tabela tem linhas separadoras com uma letra só (A, B, C...), são índices
    alfabéticos, não protocolos, e ficam de fora.
    """
    sopa = BeautifulSoup(html, "html.parser")
    tabela = sopa.find("table")
    if tabela is None:
        raise ListagemIndisponivel(
            "nenhuma tabela na página de PCDTs da Conitec; o portal pode ter mudado"
        )

    itens: list[ItemPcdt] = []
    for linha in tabela.find_all("tr"):
        celulas = linha.find_all(["td", "th"])
        if not celulas:
            continue
        condicao_bruta = " ".join(celulas[0].get_text(" ", strip=True).split())
        # Separador alfabético ou célula vazia
        if len(condicao_bruta) <= 1:
            continue
        condicao, nota = separar_nota(condicao_bruta)

        link_condicao = celulas[0].find("a", href=True)
        url_pdf = _absoluta(link_condicao["href"]) if link_condicao else None

        url_resumido = None
        for celula in celulas[1:]:
            link = celula.find("a", href=True)
            if link and "resumid" in celula.get_text(" ", strip=True).lower():
                url_resumido = _absoluta(link["href"])

        texto_portaria = celulas[1].get_text(" ", strip=True) if len(celulas) > 1 else ""
        itens.append(
            ItemPcdt(
                condicao=condicao,
                url_pdf=url_pdf,
                url_resumido=url_resumido,
                portaria=" ".join(texto_portaria.split()) or None,
                data_portaria=_data(texto_portaria),
                nota_atualizacao=nota,
            )
        )
    if not itens:
        raise ListagemIndisponivel("a tabela de PCDTs veio vazia")
    return itens


def parse_status_csv(conteudo_zip: bytes) -> dict[str, str]:
    """Mapa ``chave_nome`` da condição para status, a partir do CSV de dados abertos.

    A chave é frouxa (sem acento, pontuação nem nota de revisão) porque os nomes
    do CSV e da tabela do portal não são escritos do mesmo jeito. Mesmo assim o
    casamento é parcial: em 24/09/2026, 43 dos 132 PCDTs da listagem acharam
    status; boa parte dos nomes do CSV não existe na listagem.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as arquivo:
            nome = arquivo.namelist()[0]
            texto = arquivo.read(nome).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, IndexError) as erro:
        raise ListagemIndisponivel("CSV de status não era um zip válido") from erro

    mapa: dict[str, str] = {}
    for linha in csv.DictReader(io.StringIO(texto), delimiter=";"):
        nome_pcdt = (linha.get("nome") or "").strip()
        status = (linha.get("status") or "").strip()
        if nome_pcdt and status:
            mapa[chave_nome(nome_pcdt)] = status
    return mapa


async def baixar_listagem(client: httpx.AsyncClient | None = None) -> list[ItemPcdt]:
    """Baixa e parseia a tabela de PCDTs."""
    proprio = client is None
    http = client or httpx.AsyncClient(
        timeout=120.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        resposta = await http.get(URL_LISTAGEM)
        resposta.raise_for_status()
        return parse_listagem(resposta.text)
    finally:
        if proprio:
            await http.aclose()


async def baixar_status(client: httpx.AsyncClient | None = None) -> dict[str, str]:
    """Baixa o CSV de status. Falha aqui não impede o resto: devolve vazio."""
    proprio = client is None
    http = client or httpx.AsyncClient(
        timeout=120.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        resposta = await http.get(URL_STATUS_CSV)
        resposta.raise_for_status()
        return parse_status_csv(resposta.content)
    except (httpx.HTTPError, ListagemIndisponivel) as erro:
        logger.warning("status dos PCDTs indisponível: %s", erro)
        return {}
    finally:
        if proprio:
            await http.aclose()
