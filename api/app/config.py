"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str
    data_dir: Path
    cors_origins: tuple[str, ...]


def get_settings() -> Settings:
    default_data_dir = Path(__file__).resolve().parents[1] / "data"
    origins = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    )
    return Settings(
        app_name="NYC 311 Triage Desk API",
        data_dir=Path(os.getenv("API_DATA_DIR", default_data_dir)).resolve(),
        cors_origins=origins,
    )
