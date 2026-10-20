"""Shim chargé par xcore sous `xcore_plugin_xcore-doctype.main` (doc/doctype.md D1, §14 bis.1).

xcore purge ce module de `sys.modules` au reload, mais pas `xcore_doctype` : tout état vit donc
dans l'instance de `Plugin` (créé à `on_load`, rendu à `on_unload`), jamais au niveau d'un module.
"""

from xcore_doctype.plugin import Plugin  # noqa: F401
