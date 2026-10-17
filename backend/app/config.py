
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent.parent


def load_environment() -> bool:
    """Load the backend's local environment file without overriding process env."""
    return load_dotenv(BACKEND_DIR / ".env", override=False)


# Load once from a stable path so every module sees the same environment,
# regardless of the process working directory.
load_environment()


class Settings(BaseSettings):
    groq_api_key: str = ""

    model_config = SettingsConfigDict(extra="ignore")


settings = Settings()
