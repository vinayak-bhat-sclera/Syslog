# app/config.py
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ─────────────────────────────
    # Database Configuration
    # ─────────────────────────────
    DB_USER: str = Field("root", env="DB_USER")
    DB_PASSWORD: str = Field("Mypass123", env="DB_PASSWORD")
    DB_HOST: str = Field("127.0.0.1", env="DB_HOST")
    DB_PORT: int = Field(3306, env="DB_PORT")
    DB_NAME: str = Field("syslog_new", env="DB_NAME")
    DB_POOL_NAME: str = Field("syslog_pool", env="DB_POOL_NAME")
    DB_POOL_SIZE: int = Field(5, env="DB_POOL_SIZE")

    # ─────────────────────────────
    # Application Settings
    # ─────────────────────────────
    SYSLOG_PORT: int = Field(6666, env="SYSLOG_PORT")
    SIMILARITY_THRESHOLD: float = Field(0.6, env="SIMILARITY_THRESHOLD")
    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")


    # ─────────────────────────────
    # Spring Boot Service
    # ─────────────────────────────
    SPRINGBOOT_HOST: str = Field("localhost", env="SPRINGBOOT_HOST")
    SPRINGBOOT_PORT: int = Field(8080, env="SPRINGBOOT_PORT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
