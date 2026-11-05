#!/usr/bin/env python3
"""Copie les sous-modules vendor/ dans l'arborescence embarquée du paquet.

Les dépôts amont restent autonomes ; les adaptations du monorepo (imports
`xcore_doctype.ext.*`, `requires` de l'hôte, `desk_url` de développement et les
fichiers propres à l'embarquement) sont appliquées ici, jamais poussées.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"


@dataclass(frozen=True)
class Module:
    name: str
    source: str
    target: str
    excludes: tuple[str, ...] = ()
    rewrites: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    overlay: str | None = None
    init: str | None = None


MODULES: tuple[Module, ...] = (
    Module(
        "pubsub",
        "pubsub",
        "src/xcore_doctype/ext/pubsub",
        excludes=(
            ".gitignore",
            "GEMINI.md",
            "INTEGRATION.md",
            "contributor.yaml",
            "requirements.txt",
            "service.yaml",
        ),
    ),
    Module(
        "xstorage",
        "xstorage",
        "src/xcore_doctype/ext/xstorage",
        rewrites={
            "main.py": (("extensions.xstorage", "xcore_doctype.ext.xstorage"),),
        },
        init="Extension xstorage (voir README.md).",
    ),
    Module(
        "xwebsocket",
        "xwebsocket",
        "src/xcore_doctype/ext/xwebsocket",
        excludes=(".gitignore", "requirements.txt"),
        rewrites={
            "service.yaml": (
                ("extensions.xwebsocket", "xcore_doctype.ext.xwebsocket"),
                (
                    "https://github.com/xcore-team/xwebsocket",
                    "https://github.com/traoreera/xcore",
                ),
            ),
            "INTEGRATION.md": (
                ("extensions.xwebsocket", "xcore_doctype.ext.xwebsocket"),
            ),
            "README.md": (
                ("extensions.xwebsocket", "xcore_doctype.ext.xwebsocket"),
            ),
        },
        init="Extension xwebsocket (voir README.md).",
    ),
    Module(
        "xpay",
        "xpay",
        "src/xcore_doctype/ext/xpay",
        init="Extension xpay (voir README.md).",
    ),
    Module(
        "xmailler",
        "xmailler",
        "extensions/xmailler",
        excludes=(".gitignore",),
    ),
    Module(
        "xflow",
        "xflow",
        "src/xcore_doctype/platform/xflow",
        excludes=(
            ".gitignore",
            "contributor.yaml",
            "plugin.sig",
            "pytest.ini",
            "src/runtime/Untitled-1.json",
        ),
        rewrites={
            "plugin.yaml": (("requires: [auth]", "requires: [auth, xcore-doctype]"),),
        },
    ),
    Module(
        "xlicense",
        "xlicense",
        "src/xcore_doctype/platform/xlicense",
        excludes=(".gitignore", "plugin.sig"),
        rewrites={
            "plugin.yaml": (
                ("requires: [auth]", "requires: [xcore-doctype, auth]"),
                (
                    '  desk_url: ""',
                    '  desk_url: "http://localhost:8000/app/xcore-doctype/desk"',
                ),
            ),
        },
        overlay="adaptations/xlicense",
    ),
    Module(
        "xpulse",
        "xpulse",
        "src/xcore_doctype/platform/xpulse",
        excludes=(".gitignore", "GEMINI.md", "LICENSE", "plugin.sig"),
        overlay="adaptations/xpulse",
        rewrites={
            "src/utils.py": (
                ("extensions.extpubsub", "xcore_doctype.ext.pubsub"),
            ),
            "src/bridge/billing.py": (
                ("extensions.extpubsub", "xcore_doctype.ext.pubsub"),
            ),
            "src/bridge/handlers.py": (
                ("extensions.extpubsub", "xcore_doctype.ext.pubsub"),
            ),
            "src/routes/sse.py": (
                ("extensions.extpubsub", "xcore_doctype.ext.pubsub"),
            ),
        },
    ),
    Module(
        "xprint",
        "xprint",
        "src/xcore_doctype/platform/xprint",
    ),
)


def files_under(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in {".git", ".github", "__pycache__", "tests"} for part in rel.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        yield rel


def excluded(rel: Path, patterns: tuple[str, ...]) -> bool:
    text = rel.as_posix()
    return any(text == item or text.startswith(item + "/") for item in patterns)


def copy_tree(source: Path, target: Path, excludes: tuple[str, ...]) -> int:
    count = 0
    for rel in files_under(source):
        if excluded(rel, excludes):
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, destination)
        count += 1
    return count


def apply_rewrites(target: Path, rewrites: dict[str, tuple[tuple[str, str], ...]]) -> None:
    for name, rules in rewrites.items():
        path = target / name
        if not path.is_file():
            raise SystemExit(f"{path} absent : la règle de réécriture ne s'applique pas")
        text = path.read_text(encoding="utf-8")
        for old, new in rules:
            if old not in text:
                raise SystemExit(f"{path} : motif introuvable {old!r}")
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def sync(module: Module) -> None:
    source = VENDOR / module.source
    target = ROOT / module.target
    if not source.is_dir() or not any(source.iterdir()):
        raise SystemExit(
            f"vendor/{module.source} est vide : git submodule update --init --recursive"
        )

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    count = copy_tree(source, target, module.excludes)
    if module.overlay:
        count += copy_tree(VENDOR / module.overlay, target, ())

    apply_rewrites(target, module.rewrites)

    if module.init and not (target / "__init__.py").exists():
        (target / "__init__.py").write_text(
            f'"""{module.init}"""\n', encoding="utf-8"
        )

    print(f"{module.name:11} {count:3} fichiers -> {module.target}")


def main() -> int:
    for module in MODULES:
        sync(module)
    return 0


if __name__ == "__main__":
    sys.exit(main())
