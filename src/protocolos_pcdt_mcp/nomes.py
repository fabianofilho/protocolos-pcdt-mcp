"""Normalização dos nomes de PCDT.

A tabela da Conitec às vezes pendura no nome da condição uma nota de revisão,
por exemplo "Asma (anexo alterado em 04/09/2026)" ou "Osteoporose - portaria
atualizada em 29/01/2026". Essa nota muda a cada revisão; se ela entrar no
identificador, o identificador muda junto e "asma" deixa de achar a asma.
"""

from __future__ import annotations

import re
import unicodedata

# Nota de revisão no fim do nome, com ou sem parênteses, precedida ou não de
# hífen ou ponto: "(anexo alterado em DD/MM/AAAA)", "- alterado em DD/MM/AAAA",
# "(portaria atualizada em DD/MM/AAAA)".
_NOTA_REVISAO = re.compile(
    r"[\s.\-–]*\(?\s*"
    r"(?P<nota>(?:anexo\s+|portaria\s+)?(?:alterad[oa]|atualizad[oa]|republicad[oa])"
    r"\s+em\s+\d{1,2}º?/\d{2}/\d{4})"
    r"\s*\)?\s*$",
    re.IGNORECASE,
)


def separar_nota(condicao: str) -> tuple[str, str | None]:
    """Separa o nome da condição da nota de revisão, quando houver."""
    texto = " ".join(condicao.split())
    achado = _NOTA_REVISAO.search(texto)
    if achado is None:
        return texto, None
    nome = texto[: achado.start()].rstrip(" .-–")
    if not nome:
        return texto, None
    return nome, achado.group("nota")


def identificador_de(condicao: str) -> str:
    """Chave estável: nome sem nota de revisão, espaços colapsados, minúsculas."""
    nome, _ = separar_nota(condicao)
    return nome.lower()


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def chave_nome(texto: str) -> str:
    """Chave de comparação frouxa: sem nota, sem acento, sem pontuação.

    Usada para casar nomes de fontes diferentes (a tabela do portal e o CSV de
    dados abertos) e para aceitar um identificador digitado sem a nota.
    """
    nome, _ = separar_nota(texto)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento(nome).lower()).split())


# Siglas comuns que não aparecem no nome dos PCDTs. A sigla inteira é trocada
# pelo nome como a Conitec escreve (sem acento).
_SIGLAS = {
    "has": "hipertensao arterial sistemica",
    "dpoc": "doenca pulmonar obstrutiva cronica",
    "dm": "diabete melito",
    "dm1": "diabete melito tipo 1",
    "dm2": "diabete melito tipo 2",
    "tdah": "transtorno do deficit de atencao",
    "les": "lupus eritematoso sistemico",
    "ela": "esclerose lateral amiotrofica",
    "ic": "insuficiencia cardiaca",
    "icc": "insuficiencia cardiaca",
    "drc": "doenca renal cronica",
    "tea": "transtorno do espectro do autismo",
    "hpn": "hemoglobinuria paroxistica noturna",
    "ame": "atrofia muscular espinhal",
    "lmc": "leucemia mieloide cronica",
    "sop": "sindrome de ovarios policisticos",
    "gist": "tumor do estroma gastrointestinal",
    "dmri": "degeneracao macular relacionada a idade",
    "aids": "hiv",
}

# Grafias diferentes da mesma palavra. A Conitec escreve "diabete melito".
_VARIANTES = {
    "diabetes": ("diabete",),
    "mellitus": ("melito", "mellitus"),
    "melitus": ("melito", "mellitus"),
    "melito": ("melito", "mellitus"),
}

_PALAVRAS_VAZIAS = frozenset(
    "a o as os e de da do das dos em na no nas nos com para por pelo pela ao aos".split()
)


def termos_de_busca(termo: str) -> list[tuple[str, ...]]:
    """Termo digitado para uma lista de palavras, cada uma com suas grafias.

    Todas as palavras precisam aparecer no nome, em qualquer ordem; basta uma
    das grafias de cada palavra. Siglas conhecidas viram o nome por extenso.
    """
    base = " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento(termo).lower()).split())
    base = _SIGLAS.get(base, base)
    palavras = [p for p in base.split() if p not in _PALAVRAS_VAZIAS]
    return [_VARIANTES.get(p, (p,)) for p in palavras]
