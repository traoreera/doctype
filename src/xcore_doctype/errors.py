"""Erreurs du moteur : un code stable et un statut HTTP, jamais un texte à analyser."""

from __future__ import annotations

from typing import Any


class DoctypeError(Exception):
    """Erreur métier. `code` est contractuel, `message` est destiné à l'humain."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        status: int = 400,
        details: Any = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status
        self.details = details

    def as_detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "msg": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out


class ContextError(DoctypeError):
    """Identité ou tenant refusé (annexe A)."""

    def __init__(self, code: str, status: int = 403, message: str | None = None) -> None:
        super().__init__(code, message, status=status)


class PermissionDenied(DoctypeError):
    """Aucune règle n'accorde l'action. Volontairement sans détail sur les rôles attendus."""

    def __init__(self) -> None:
        super().__init__("permission_denied", "Accès refusé", status=403)


class NotFound(DoctypeError):
    def __init__(self, code: str = "not_found", message: str | None = None) -> None:
        super().__init__(code, message, status=404)


class VersionConflict(DoctypeError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__("version_conflict", message, status=409)


class DocumentInUse(DoctypeError):
    """Suppression refusée : d'autres documents pointent encore vers celui-ci (champs Link)."""

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__("document_in_use", message, status=409, details=details)


class DocumentCited(DoctypeError):
    """Annulation refusée : un document SOUMIS cite encore celui-ci (une facture payée, un paiement qui tient)."""

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__("document_cited", message, status=409, details=details)


class DocumentLocked(DoctypeError):
    """Modification ou suppression d'un document qui n'est plus un brouillon : soumis ou annulé, il est figé."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__("document_locked", message, status=409)


class InvalidDocStatus(DoctypeError):
    """Soumettre, annuler ou amender un document qui n'est pas dans l'état de départ de cette transition."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__("invalid_docstatus", message, status=409)


class AlreadyExists(DoctypeError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__("already_exists", message, status=409)


class ValidationFailed(DoctypeError):
    def __init__(
        self, message: str | None = None, *, code: str = "validation_failed", details: Any = None
    ) -> None:
        super().__init__(code, message, status=422, details=details)


class DocumentValidationError(ValidationFailed):
    """À lever dans un hook `validate` ou `before_save` pour annuler la sauvegarde (422, D2).

    Le message est destiné à l'utilisateur final : il est renvoyé tel quel. Pas de détail interne.
    """

    def __init__(self, message: str, *, code: str = "hook_rejected", details=None) -> None:
        super().__init__(message, code=code, details=details)


class HookRegistrationError(ValueError):
    """Nom de hook invalide, déjà pris, ou fonction non asynchrone."""


class InvalidDefinition(Exception):
    """Définition de DocType rejetée au chargement (jamais une erreur HTTP)."""


class ConfigurationError(Exception):
    """Déploiement inutilisable : levée au `on_load`, le plugin ne se charge pas."""
