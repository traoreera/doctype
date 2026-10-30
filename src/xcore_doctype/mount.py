from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI



def _find_desk_dir() -> Path:
    here = Path(__file__).resolve().parent  # src/xcore_doctype
    # 1) Mode dev : ui/dist à la racine du repo
    cand = here.parents[1] / "ui" / "dist"
    if cand.is_dir():
        return cand
    # 2) Mode wheel / installé dans le package
    cand = here / "ui" / "dist"
    if cand.is_dir():
        return cand
    cand = here / "desk" / "dist"
    if cand.is_dir():
        return cand
    # 3) Fallback au cas où
    return cand


@dataclass
class Config:
    path: str = "/"
    api_base: str = "http://localhost:8000/app/xcore-doctype"
    login_url: str = "http://localhost:8000/app/auth/login"
    developer: bool = False
    extension: list[str] = field(default_factory=list)
    fallback: Optional[Literal["auto", "index.html", "404.html"]] = "auto"
    check_dir: bool | Literal["auto"] = "auto"
    config_url: str = "/config.json"

    # Seuls ces champs sont exposés au navigateur
    _public = ("api_base", "login_url", "extension", "developer")

    def public(self) -> dict:
        data = asdict(self)
        return {k: data[k] for k in self._public}


def mount_desk(app: FastAPI, config: Config, **route):
    prefix = config.path.rstrip("/")

    # 1. La route de config d'abord, pour qu'elle ne soit pas masquée
    @app.get(f"{prefix}{config.config_url}", **route)
    async def desk_config():
        return config.public()

    # 2. Ensuite le frontend
    app.frontend(
        config.path,
        directory=_find_desk_dir(),
        fallback=config.fallback,
        check_dir=config.check_dir,
    )