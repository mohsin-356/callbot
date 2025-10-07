from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    PORT: int = 5000
    MODE: str = "development"

    # CORS / Frontend
    FRONTEND_URL: str = "http://localhost:3000"
    ALLOW_CORS_ANY: bool = True

    # Datastores (local defaults)
    MONGO_URI: str = "mongodb://localhost:27017/callbot"

    # Messaging / Queue
    REDIS_URL: str = "redis://localhost:6379/0"

    # NLP (Rasa)
    RASA_URL: str = "http://localhost:5005"

    # Media tools
    FFMPEG_BIN: str | None = None  # e.g., C:\ffmpeg\...\bin\ffmpeg.exe
    VOSK_MODEL_DIR: str | None = None  # e.g., C:\path\to\vosk\model

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
