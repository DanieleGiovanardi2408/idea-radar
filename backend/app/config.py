"""Configurazione dell'applicazione letta da variabili d'ambiente / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    github_token: str = ""
    # Token developer GRATUITO di Product Hunt (https://api.producthunt.com/v2/docs).
    # Obbligatorio per la fonte "producthunt": senza, il collector alza errore.
    producthunt_token: str | None = None
    ollama_host: str = "http://localhost:11434"
    # Modello per gli insight testuali (summary/why/difficulty).
    ollama_model: str = "qwen2.5:7b"
    # Modello per gli embedding (clustering e topic). `ollama pull nomic-embed-text`.
    embedding_model: str = "nomic-embed-text"
    # Chiave GRATUITA per la YouTube Data API v3 (console.cloud.google.com):
    # serve solo al pannello video del Radar, che senza si spegne da sé con un
    # messaggio invece di sembrare rotto. Nessuna parte della pipeline la usa.
    youtube_api_key: str = ""
    # Se True la pipeline fallisce quando Ollama è irraggiungibile;
    # se False (default) ripiega su un insight euristico e prosegue.
    llm_required: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
