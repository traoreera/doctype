"""xcore-doctype : moteur de DocTypes pour xcore.

API publique pour les auteurs de hooks (doc/doctype.md D2, D16) :

    from xcore_doctype import DocumentValidationError, HookEvent

    async def check_credit(event: HookEvent) -> None:
        if event.data.get("credit_limit", 0) > 100_000:
            raise DocumentValidationError("Plafond trop élevé")

    # dans on_load d'un plugin qui déclare `requires: [xcore-doctype]` :
    self.get_service("doctype_hooks").register("sales.check_credit", check_credit, owner=self.ctx.name)
    # dans on_unload :
    self.get_service("doctype_hooks").unregister_owner(self.ctx.name)

Puis, dans la définition du DocType : `hooks: {validate: ["sales.check_credit"]}`.

Un plugin métier apporte aussi ses DocTypes (leur `module` doit être le nom du plugin) :

    self.get_service("doctype_registry").load_dir(self.ctx.name, Path(__file__).parent.parent / "doctypes")   # on_load
    self.get_service("doctype_registry").unregister_owner(self.ctx.name)                                       # on_unload
"""

from importlib import metadata, resources
from pathlib import Path

from .errors import DocumentValidationError, HookRegistrationError
from .hooks import HookEvent, HookRegistry

try:
    __version__ = metadata.version("xcore-doctype")
except metadata.PackageNotFoundError:  # exécuté depuis des sources non installées
    __version__ = "0+unknown"


def plugin_dir() -> Path:
    """Le dossier de plugin embarqué (`plugin.yaml` + `src/main.py`), dans le paquet installé.

    C'est aussi la cible de l'entry point `xcore.plugins` que le paquet déclare (doc/doctype.md D1) : xcore ne le lit pas
    encore (il ne découvre que des dossiers), `xcore-doctype install` pose donc une copie dans l'instance. Voir doc/phase4.md.
    """
    return Path(str(resources.files("xcore_doctype") / "plugin_dir"))


def system_plugin_dir() -> Path:
    """Le plugin `system` embarqué (réglages globaux : `System Settings`), que `xcore-doctype install` dépose à côté du moteur."""
    return Path(str(resources.files("xcore_doctype") / "system_plugin_dir"))


__all__ = ["DocumentValidationError", "HookEvent", "HookRegistrationError", "HookRegistry", "plugin_dir", "system_plugin_dir", "__version__"]
