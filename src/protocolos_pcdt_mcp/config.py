"""Configuração lida do ambiente (.env)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qwen_endpoint: str = Field(default="http://127.0.0.1:8080/v1")
    qwen_model: str = Field(default="local-model")
    qwen_timeout_segundos: float = Field(default=180.0)

    duckdb_path: Path = Field(default=Path("./data/pcdt.duckdb"))
    coleta_delay_segundos: float = Field(default=1.0, ge=0.0)
    log_level: str = Field(default="INFO")


def carregar_config() -> Config:
    return Config()
