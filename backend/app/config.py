"""Configurazione dell'applicazione letta da variabili d'ambiente / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    github_token: str = ""
    ollama_host: str = "http://localhost:11434"
    # Modello per gli insight testuali (summary/why/difficulty).
    ollama_model: str = "qwen2.5:7b"
    # Modello per gli embedding (clustering e topic). `ollama pull nomic-embed-text`.
    embedding_model: str = "nomic-embed-text"
    # Se True la pipeline fallisce quando Ollama è irraggiungibile;
    # se False (default) ripiega su un insight euristico e prosegue.
    llm_required: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
