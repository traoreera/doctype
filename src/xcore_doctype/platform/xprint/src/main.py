"""xprint : charge les gabarits d'impression livrés en YAML (`print/<DocType>/*.yaml`) pour les DocTypes présents.

Un dossier par DocType, nommé comme lui : `load_print_templates` est « tout ou rien » par dossier, et un type absent (le module
n'est pas installé) ne doit pas empêcher les autres de charger. Un gabarit FAUTIF, lui, fait échouer le chargement : c'est un
défaut à corriger, pas un cas à taire (le test `tests/unit/test_xprint.py` les charge tous contre le jeu `doctypes/`).
"""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

PRINT = Path(__file__).resolve().parent.parent / "print"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        registry = self.get_service("doctype_registry")
        loaded: list[str] = []
        skipped: list[str] = []
        for folder in sorted(p for p in PRINT.iterdir() if p.is_dir()):
            if registry.has(folder.name):
                loaded += registry.load_print_templates(self.ctx.name, folder)
            else:
                skipped.append(folder.name)
        self.logger.info("xprint loaded", templates=loaded, skipped_doctypes=skipped)

    async def on_unload(self) -> None:
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
