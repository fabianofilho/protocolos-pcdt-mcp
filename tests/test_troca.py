"""Publicação por troca atômica: compactação, preservação e recusa de base menor.

O motivo de a troca existir aqui é o inchaço, e aqui ele é o pior dos MCPs desta
máquina: a coleta diária reescreve o texto completo dos 132 protocolos, e o DuckDB
não devolve o espaço das páginas antigas. Medido em 11/10/2026, o arquivo servido
estava em 1.062 MB e a cópia compactada em 49 MB.
O que os testes abaixo prendem é o comportamento que dá essa folga sem perder
dado no caminho.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from protocolos_pcdt_mcp.store.db import aplicar_schema
from protocolos_pcdt_mcp.store.troca import (
    BaseSuspeita,
    caminho_em_construcao,
    clonar_para_construcao,
    publicar,
    reverter,
)

_TABELA = "protocolos"


def _base(caminho: Path, quantas: int, *, marca: str = "condicao") -> None:
    conexao = duckdb.connect(str(caminho))
    aplicar_schema(conexao)
    conexao.executemany(
        f"INSERT INTO {_TABELA} (identificador, condicao) VALUES (?, ?)",
        [[f"pcdt-{i:05d}", f"{marca} {i}"] for i in range(quantas)],
    )
    conexao.close()


def _quantas(caminho: Path) -> int:
    conexao = duckdb.connect(str(caminho), read_only=True)
    try:
        linha = conexao.execute(f"SELECT count(*) FROM {_TABELA}").fetchone()
        return int(linha[0]) if linha else 0
    finally:
        conexao.close()


# --- o inchaço, que é o motivo de a troca existir neste projeto ----------


def test_clone_compacta_a_base_inchada(tmp_path: Path) -> None:
    """Upsert repetido no arquivo servido só cresce; a cópia devolve o tamanho real."""
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 3000)
    for _ in range(20):
        conexao = duckdb.connect(str(servida))
        conexao.execute(f"UPDATE {_TABELA} SET condicao = condicao || 'x'")
        conexao.close()
    inchada = servida.stat().st_size

    nova = clonar_para_construcao(servida)

    assert nova.stat().st_size < inchada, "o clone tem que sair menor que a base inchada"
    assert _quantas(nova) == 3000, "menor em bytes, igual em linhas"


def test_clone_nao_perde_o_que_a_coleta_de_hoje_nao_trouxe(tmp_path: Path) -> None:
    """A base nova nasce como cópia: construir do zero publicaria só o download de hoje."""
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 5, marca="de-ontem")

    nova = clonar_para_construcao(servida)

    conexao = duckdb.connect(str(nova), read_only=True)
    try:
        assert conexao.execute(
            f"SELECT count(*) FROM {_TABELA} WHERE condicao LIKE 'de-ontem%'"
        ).fetchone() == (5,)
    finally:
        conexao.close()


def test_clone_sem_base_servida_nao_e_erro(tmp_path: Path) -> None:
    """Primeira coleta da vida: não há de onde clonar, e o caminho volta vazio."""
    assert not clonar_para_construcao(tmp_path / "ainda-nao-existe.duckdb").exists()


# --- a troca em si -------------------------------------------------------


def test_escritor_e_bloqueado_por_leitor(tmp_path: Path) -> None:
    """Escrever direto no arquivo servido falha: é a outra razão de existir a troca."""
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 2)

    leitor = duckdb.connect(str(servida), read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            duckdb.connect(str(servida))
    finally:
        leitor.close()


def test_troca_funciona_com_leitor_aberto(tmp_path: Path) -> None:
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 10)
    _base(caminho_em_construcao(servida), 10, marca="novo")

    leitor = duckdb.connect(str(servida), read_only=True)
    try:
        publicar(servida)  # não levanta, mesmo com leitor aberto
    finally:
        leitor.close()

    conexao = duckdb.connect(str(servida), read_only=True)
    try:
        assert conexao.execute(
            f"SELECT count(*) FROM {_TABELA} WHERE condicao LIKE 'novo%'"
        ).fetchone() == (10,)
    finally:
        conexao.close()


def test_publicar_guarda_a_anterior(tmp_path: Path) -> None:
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 10)
    _base(caminho_em_construcao(servida), 10)

    publicar(servida)

    assert (tmp_path / "pcdt.duckdb.anterior").exists()
    assert not caminho_em_construcao(servida).exists()


def test_reverter_volta_a_anterior(tmp_path: Path) -> None:
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 10, marca="antiga")
    _base(caminho_em_construcao(servida), 10, marca="nova")
    publicar(servida)

    reverter(servida)

    conexao = duckdb.connect(str(servida), read_only=True)
    try:
        assert conexao.execute(
            f"SELECT count(*) FROM {_TABELA} WHERE condicao LIKE 'antiga%'"
        ).fetchone() == (10,)
    finally:
        conexao.close()


def test_publicar_sem_base_nova_falha_explicito(tmp_path: Path) -> None:
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 2)
    with pytest.raises(FileNotFoundError) as erro:
        publicar(servida)
    assert "nada para publicar" in str(erro.value)


def test_em_construcao_fica_no_mesmo_diretorio(tmp_path: Path) -> None:
    """os.replace só é atômico dentro do mesmo sistema de arquivos."""
    servida = tmp_path / "sub" / "pcdt.duckdb"
    assert caminho_em_construcao(servida).parent == servida.parent


# --- salvaguarda contra publicar base truncada ---------------------------


def test_recusa_publicar_base_que_encolheu(tmp_path: Path) -> None:
    """Coleta truncada por rede ruim não pode substituir a base boa em silêncio."""
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 1000)
    _base(caminho_em_construcao(servida), 20)

    with pytest.raises(BaseSuspeita) as erro:
        publicar(servida)

    assert "20 linha(s)" in str(erro.value) and "1000" in str(erro.value)
    assert _quantas(servida) == 1000, "a base servida continua intacta"


def test_forcar_publica_mesmo_menor(tmp_path: Path) -> None:
    """Quando a fonte encolheu de verdade, quem decide é quem roda."""
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 100)
    _base(caminho_em_construcao(servida), 5)

    publicar(servida, forcar=True)

    assert _quantas(servida) == 5


def test_pequena_variacao_para_menos_e_tolerada(tmp_path: Path) -> None:
    """Protocolo sai do portal quando é substituído: 4% a menos não é alarme."""
    servida = tmp_path / "pcdt.duckdb"
    _base(servida, 1000)
    _base(caminho_em_construcao(servida), 960)
    publicar(servida)  # não levanta


def test_primeira_publicacao_nao_exige_forcar(tmp_path: Path) -> None:
    """Sem base servida ainda, não há com o que comparar."""
    servida = tmp_path / "pcdt.duckdb"
    _base(caminho_em_construcao(servida), 10)
    publicar(servida)
    assert servida.exists()
