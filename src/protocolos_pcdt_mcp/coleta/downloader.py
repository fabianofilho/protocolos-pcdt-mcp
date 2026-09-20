"""Download dos PDFs de protocolo, com cache por URL."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import httpx

from protocolos_pcdt_mcp.coleta.listagem import USER_AGENT

logger = logging.getLogger(__name__)


class CachePdf:
    """Guarda os PDFs em disco, nomeados pelo hash da URL."""

    def __init__(self, diretorio: Path | str) -> None:
        self._dir = Path(diretorio)

    def _caminho(self, url: str) -> Path:
        return self._dir / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.pdf"

    def tem(self, url: str) -> bool:
        return self._caminho(url).exists()

    def ler(self, url: str) -> bytes | None:
        caminho = self._caminho(url)
        return caminho.read_bytes() if caminho.exists() else None

    def gravar(self, url: str, conteudo: bytes) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        caminho = self._caminho(url)
        caminho.write_bytes(conteudo)
        return caminho


class Downloader:
    """Baixa PDFs respeitando um intervalo entre requisições."""

    def __init__(
        self,
        cache: CachePdf,
        *,
        delay_segundos: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache = cache
        self._delay = delay_segundos
        self._client = client
        self._client_proprio = client is None

    async def __aenter__(self) -> Downloader:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=180.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client_proprio:
            await self._client.aclose()
            self._client = None

    async def obter(self, url: str) -> bytes | None:
        """PDF do cache, ou baixado agora. ``None`` quando o download falha."""
        cacheado = self._cache.ler(url)
        if cacheado is not None:
            return cacheado

        if self._client is None:
            raise RuntimeError("Downloader precisa ser usado como 'async with'")

        await asyncio.sleep(self._delay)
        try:
            resposta = await self._client.get(url)
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            logger.warning("download falhou (%s): %s", type(erro).__name__, url[:80])
            return None

        conteudo = resposta.content
        self._cache.gravar(url, conteudo)
        return conteudo
