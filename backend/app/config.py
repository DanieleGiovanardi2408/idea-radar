"""Configurazione dell'applicazione letta da variabili d'ambiente / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    github_token: str = ""
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
