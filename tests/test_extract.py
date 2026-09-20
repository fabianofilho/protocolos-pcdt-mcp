"""Extração e segmentação do PDF de protocolo."""

from __future__ import annotations

from protocolos_pcdt_mcp.extract.parser import extrair, limpar, segmentar

PROTOCOLO = """PROTOCOLO CLÍNICO E DIRETRIZES TERAPÊUTICAS

1 INTRODUÇÃO
A condição acomete adultos.

2 DIAGNÓSTICO
Serão incluídos pacientes com achado X.

3 TRATAMENTO
Primeira linha: fármaco A, 500 mg, duas vezes ao dia.

4 MONITORAMENTO
Reavaliar em 30 dias.
"""


def test_segmenta_secoes_conhecidas() -> None:
    secoes = segmentar(PROTOCOLO)
    assert set(secoes) >= {"introducao", "diagnostico", "tratamento", "monitoramento"}
    assert "fármaco A" in secoes["tratamento"]


def test_secao_nao_vaza_para_a_seguinte() -> None:
    secoes = segmentar(PROTOCOLO)
    assert "Reavaliar" not in secoes["tratamento"]


def test_texto_sem_estrutura_degrada_para_vazio() -> None:
    """Requisito: degradar para texto corrido em vez de inventar seção."""
    assert segmentar("Texto corrido sem títulos reconhecíveis.") == {}


def test_pdf_vazio_sinaliza() -> None:
    resultado = extrair(b"")
    assert resultado.extracao_incompleta is True
    assert resultado.motivo is not None


def test_pdf_invalido_nao_levanta() -> None:
    resultado = extrair(b"nao sou pdf")
    assert resultado.extracao_incompleta is True


def test_limpar_normaliza() -> None:
    assert limpar("a  b\n\n\n\nc") == "a b\n\nc"
