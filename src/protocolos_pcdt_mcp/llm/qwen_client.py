"""Cliente do endpoint OpenAI-compatible do ODS (Qwen local)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_JSON_NA_RESPOSTA = re.compile(r"\{.*\}", re.DOTALL)


class QwenIndisponivel(RuntimeError):
    """O LLM local não respondeu, ou respondeu algo inutilizável."""


class QwenClient:
    """Chat completions com timeout e retry com backoff exponencial."""

    def __init__(
        self,
        endpoint: str,
        modelo: str,
        *,
        timeout_segundos: float = 180.0,
        max_tentativas: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._modelo = modelo
        self._timeout = timeout_segundos
        self._max_tentativas = max_tentativas
        self._client = client
        self._client_proprio = client is None

    async def __aenter__(self) -> QwenClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client_proprio:
            await self._client.aclose()
            self._client = None

    async def esta_vivo(self) -> bool:
        try:
            resposta = await self._exigir_client().get(f"{self._endpoint}/models", timeout=5.0)
            return resposta.status_code == 200
        except httpx.HTTPError as erro:
            logger.warning("LLM local não respondeu: %s", erro)
            return False

    async def pedir_json(self, sistema: str, usuario: str) -> dict[str, Any]:
        """Uma chamada com resposta em JSON, com retry."""
        cliente = self._exigir_client()
        payload = {
            "model": self._modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "temperature": 0.1,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
        }

        ultimo: Exception | None = None
        for tentativa in range(1, self._max_tentativas + 1):
            try:
                resposta = await cliente.post(
                    f"{self._endpoint}/chat/completions", json=payload, timeout=self._timeout
                )
                resposta.raise_for_status()
                return self._parse(str(resposta.json()["choices"][0]["message"]["content"]))
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as erro:
                ultimo = erro
                if tentativa == self._max_tentativas:
                    break
                await asyncio.sleep(2 ** (tentativa - 1))
        raise QwenIndisponivel(
            f"LLM local em {self._endpoint} falhou após {self._max_tentativas} tentativas: {ultimo}"
        ) from ultimo

    @staticmethod
    def _parse(conteudo: str) -> dict[str, Any]:
        bruto = conteudo.strip()
        try:
            return dict(json.loads(bruto))
        except json.JSONDecodeError:
            pass
        achado = _JSON_NA_RESPOSTA.search(bruto)
        if achado is None:
            raise QwenIndisponivel("resposta do LLM não continha JSON")
        try:
            return dict(json.loads(achado.group(0)))
        except json.JSONDecodeError as erro:
            raise QwenIndisponivel("resposta do LLM não era JSON válido") from erro

    def _exigir_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("QwenClient precisa ser usado como 'async with'")
        return self._client
