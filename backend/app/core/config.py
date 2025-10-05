from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    PORT: int = 5000
    MODE: str = "development"

    # CORS / Frontend
    FRONTEND_URL: str = "http://localhost:3000"
    ALLOW_CORS_ANY: bool = True

    # Datastores (compose currently uses Mongo)
    MONGO_URI: str = "mongodb://mongo:27017/callbot"

    # Messaging / Queue
    REDIS_URL: str = "redis://redis:6379/0"

    # NLP (Rasa)
    RASA_URL: str = "http://rasa:5005"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
