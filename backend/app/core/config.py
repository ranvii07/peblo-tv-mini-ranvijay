"""Application settings and the loaded `reference.json`.

`reference.json` is domain configuration owned by the content team, not source code:
sections, categories, languages and artwork specs are all read from it at runtime so
that adding a language or retargeting a poster size is a data change, not a deploy.
Nothing in this codebase hardcodes those values.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://peblo:peblo@localhost:5432/peblo"
    jwt_secret: str = "dev-only-not-a-real-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 12

    storage_backend: str = "local"
    storage_root: str = "./storage_data"

    r2_account_id: str = ""
    r2_bucket: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_public_base_url: str = ""

    seed_on_start: bool = True
    seed_dir: str = "../data/seed"
    autopublish_on_seed: bool = True

    admin_email: str = "admin@peblo.test"
    admin_password: str = "admin123"
    editor_email: str = "editor@peblo.test"
    editor_password: str = "editor123"


@lru_cache
def get_settings() -> Settings:
    return Settings()


class Reference:
    """Typed accessor over reference.json."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.sections: list[str] = list(raw["sections"])
        self.categories: list[str] = list(raw["categories"])
        self.languages: list[str] = list(raw["languages"])
        self.artwork_specs: dict[str, dict] = dict(raw["artwork_specs"])
        self.conventions: dict[str, str] = dict(raw.get("conventions", {}))

    def section_order(self, section: str | None) -> int:
        """Publish ordering: sections appear in reference.json order."""
        try:
            return self.sections.index(section)  # type: ignore[arg-type]
        except ValueError:
            return len(self.sections)

    def spec(self, kind: str) -> dict:
        if kind not in self.artwork_specs:
            raise KeyError(f"unknown artwork kind: {kind}")
        return self.artwork_specs[kind]


@lru_cache
def get_reference() -> Reference:
    seed_dir = Path(get_settings().seed_dir)
    path = seed_dir / "reference.json"
    if not path.is_absolute():
        path = (Path(__file__).resolve().parents[2] / path).resolve()
    with open(path, encoding="utf-8") as fh:
        return Reference(json.load(fh))
