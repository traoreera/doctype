"""Droits par champ (traoreera/doctype#74, `process/champs-reserves.md`) : la valeur d'un champ réservé ne sort ni n'entre par aucune porte.

Le modèle : `permissions: {read: [grants], write: [grants]}` sur un CHAMP, en plus des droits du document. Ce droit ne dépend que de
l'appelant (jamais du document) : il a UN interprète (`permissions.field_access`), comparé ici à un oracle écrit à part, sur tous
les jeux de grants ; et le chemin SQL et son jumeau en mémoire (types virtuels) sont comparés porte par porte.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- LIRE : fiche, liste, résultat d'une écriture, historique, corbeille, impression, fichiers, connexions, `/meta`, blocs — la clé manque ;
- ÉCRIRE : API, lot, import CSV, table enfant, type virtuel — refus nommé `field_not_writable`, et jamais d'oracle (envoyer la BONNE
  valeur d'un champ caché est refusé de la même façon que la mauvaise) ; enregistrer la fiche ne vide pas ce qu'on ne voit pas ;
- DÉDUIRE : filtre, filtre de ligne, tri, regroupement, mesure, vue enregistrée, compteur d'étiquettes — refus nommé `field_not_readable` ;
- la DÉFINITION ne peut pas révéler par un détour (titre, nom, vue, condition, calcul, copie, filtre de lien) : refus au chargement ;
- les grants des champs sont déclarés comme les autres ; un DocType sans champ réservé ne change en rien ; deux organisations s'ignorent.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from tests.unit.test_print_service import MemoryStore
from tests.unit.test_virtual_doctypes import MemorySource
from xcore_doctype import files as F
from xcore_doctype.collaboration import CollaborationService
from xcore_doctype.context import RequestContext
from xcore_doctype.csv_import import import_csv
from xcore_doctype.dashboards import NumberCardBlock
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDefinition, NotFound
from xcore_doctype.file_library import FileLibrary
from xcore_doctype.file_service import FileService
from xcore_doctype.grants import GrantCatalog
from xcore_doctype.notification_rules import RESERVED_FIELD, rule_problems
from xcore_doctype.overrides import validate_workflow_override
from xcore_doctype.permissions import field_access
from xcore_doctype.print_service import PrintService
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer
from xcore_doctype.sources import DocumentSources

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
SAL_R, SAL_W, GRADE_W, NOTES = "hr:employee:salary_read", "hr:employee:salary_write", "hr:employee:grade_write", "hr:employee:hr_notes"

BONUS = {
    "schema_version": 1, "name": "Employee Bonus", "istable": True,
    "fields": [{"fieldname": "label", "fieldtype": "Text"}, {"fieldname": "amount", "fieldtype": "Currency"}],
}
# Les cinq premiers champs servent aussi au type VIRTUEL (une source n'a ni fichier, ni secret, ni table ici).
SHARED = [
    {"fieldname": "full_name", "fieldtype": "Text", "required": True},
    {"fieldname": "department", "fieldtype": "Select", "options": ["Ventes", "Atelier"]},
    # Lire le salaire demande un grant, l'écrire un autre : trois états possibles (caché, lu seulement, écrit).
    {"fieldname": "salary", "fieldtype": "Currency", "permissions": {"read": [SAL_R], "write": [{"grant": SAL_W, "description": "Fixer le salaire"}]}},
    # Tout lecteur du document lit l'échelon ; seul un grant l'écrit.
    {"fieldname": "grade", "fieldtype": "Text", "default": "A1", "permissions": {"write": [GRADE_W]}},
    # `write` absent : qui LIT les notes les écrit.
    {"fieldname": "hr_notes", "fieldtype": "LongText", "permissions": {"read": [NOTES]}},
]
EMPLOYEE = {
    "schema_version": 1, "name": "Employee", "module": "hr", "title_field": "full_name", "naming": "prompt",
    "fields": [
        *SHARED,
        {"fieldname": "manager", "fieldtype": "Link", "options": "Employee", "permissions": {"read": [SAL_R]}},
        {"fieldname": "bonuses", "fieldtype": "Table", "options": "Employee Bonus", "permissions": {"read": [SAL_R]}},
        {"fieldname": "badge", "fieldtype": "Password", "permissions": {"read": [NOTES]}},
        {"fieldname": "contract", "fieldtype": "Attach", "permissions": {"read": [NOTES]}},
        # `[]` : personne. Seul le serveur (hooks, tâches) y touche.
        {"fieldname": "audit_ref", "fieldtype": "Text", "permissions": {"read": []}},
    ],
    "layout": [{"section": "Identité", "columns": [["full_name", "department"], ["grade"]]}, {"section": "Paie", "columns": [["salary", "manager"]]}],
}
EMPLOYEE_V = {"schema_version": 1, "name": "EmployeeV", "module": "hr", "title_field": "full_name", "source": "memory", "fields": SHARED}
# Un type SANS champ réservé : rien ne doit changer pour lui.
TEAM = {"schema_version": 1, "name": "Team", "module": "hr", "fields": [{"fieldname": "label", "fieldtype": "Text"}, {"fieldname": "budget", "fieldtype": "Number"}]}

BASE = [f"hr:{t}:{a}" for t in ("employee", "employeev", "team") for a in ("read", "write", "delete")]
HR = make_ctx("acme", "hana", permissions=[*BASE, SAL_R, SAL_W, GRADE_W, NOTES])
CLERK = make_ctx("acme", "carl", permissions=BASE)  # lit et écrit les fiches, aucun champ réservé
PAYROLL = make_ctx("acme", "paul", permissions=[*BASE, SAL_R])  # LIT le salaire, ne l'écrit pas
OTHER_HR = make_ctx("globex", "gina", permissions=[*BASE, SAL_R, SAL_W, GRADE_W, NOTES])
SYSTEM = RequestContext.system("acme", "test des droits par champ")
HIDDEN_FROM_CLERK = {"salary", "hr_notes", "manager", "bonuses", "badge", "contract", "audit_ref"}


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    for raw in (BONUS, EMPLOYEE, EMPLOYEE_V, TEAM):
        reg.register(parse_definition(raw))
    reg.validate_links()
    source = MemorySource()
    sources = DocumentSources()
    sources.register("hr", "memory", source)
    docs = DocumentService(reg, db, tables, Log(), sealer=Sealer("k" * 32), sources=sources, clock=lambda: NOW)
    files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
    docs.files = files
    collab = CollaborationService(docs, files)
    return type("Env", (), {"reg": reg, "docs": docs, "files": files, "source": source, "collab": collab,
                            "printer": PrintService(docs, files), "library": FileLibrary(docs, collab)})


FULL = {"full_name": "Ada Lovelace", "department": "Ventes", "salary": "5200.00", "grade": "B2", "hr_notes": "À promouvoir",
        "bonuses": [{"label": "Prime", "amount": "300.00"}], "badge": "s3cret"}


async def ada(env, name="ada", ctx=HR, **extra):
    return await env.docs.create(ctx, "Employee", {**FULL, **extra}, name=name)


def refused(e, code):
    assert (e.value.status, e.value.code) == (403, code), (e.value.status, e.value.code, e.value.message)
    return sorted(d["field"] for d in e.value.details)


# ── L'interprète unique, contre un oracle ─────────────────────────────────────────────────────


def oracle(held: set[str]) -> tuple[set[str], set[str]]:
    """Écrit à part de `field_access`, depuis la phrase du contrat : « lire demande l'un des grants de `read` s'il y en a ; écrire demande
    de lire, et l'un des grants de `write` s'il y en a »."""
    hidden, read_only = set(), set()
    for f in EMPLOYEE["fields"]:
        rules = f.get("permissions")
        if rules is None:
            continue
        reads = "read" not in rules or any((g if isinstance(g, str) else g["grant"]) in held for g in rules["read"])
        writes = reads and ("write" not in rules or any((g if isinstance(g, str) else g["grant"]) in held for g in rules["write"]))
        if not reads:
            hidden.add(f["fieldname"])
        elif not writes:
            read_only.add(f["fieldname"])
    return hidden, read_only


def test_field_access_matches_the_oracle_for_every_set_of_grants():
    defn = parse_definition(EMPLOYEE)
    grants = [SAL_R, SAL_W, GRADE_W, NOTES]
    for n in range(len(grants) + 1):
        for held in itertools.combinations(grants, n):
            access = field_access(defn, make_ctx("acme", "u", permissions=[*BASE, *held]))
            assert (set(access.hidden), set(access.read_only)) == oracle(set(held)), held
    # Écrire exige de lire : le grant d'écriture SEUL n'ouvre rien.
    assert "salary" in field_access(defn, make_ctx("acme", "u", permissions=[*BASE, SAL_W])).hidden
    # Le contexte système (hooks, tâches) voit et écrit tout, même ce que `[]` ferme à tous.
    assert not field_access(defn, SYSTEM)
    # Un type sans champ réservé : rien, sans même regarder les grants.
    assert not field_access(parse_definition(TEAM), make_ctx("acme", "u", permissions=[]))


# ── Lire ──────────────────────────────────────────────────────────────────────────────────────


async def test_a_reserved_value_leaves_by_no_document_answer(env):
    made = await ada(env)
    assert made["data"]["salary"] == "5200.00" and made["data"]["badge"] == "********"  # celle qui a tout voit tout (un secret reste masqué)
    seen = (await env.docs.get(CLERK, "Employee", "ada"))["data"]
    assert seen == {"full_name": "Ada Lovelace", "department": "Ventes", "grade": "B2"}  # la clé MANQUE : ni null, ni masque
    [item] = (await env.docs.list(CLERK, "Employee"))["items"]
    assert not HIDDEN_FROM_CLERK & set(item["data"])
    # Celui qui lit le salaire sans les notes : exactement ce que ses grants ouvrent.
    assert set((await env.docs.get(PAYROLL, "Employee", "ada"))["data"]) == {"full_name", "department", "grade", "salary", "bonuses"}
    # Le résultat d'une ÉCRITURE est une réponse comme une autre.
    updated = await env.docs.update(CLERK, "Employee", "ada", {**seen, "department": "Atelier"}, made["version"])
    assert not HIDDEN_FROM_CLERK & set(updated["data"])
    created = await env.docs.create(CLERK, "Employee", {"full_name": "Bob"}, name="bob")
    assert not HIDDEN_FROM_CLERK & set(created["data"])


async def test_saving_the_form_does_not_erase_what_the_caller_cannot_see(env):
    """Une mise à jour REMPLACE `data` : sans la remise en place par le serveur, le premier enregistrement d'un commis viderait le salaire."""
    made = await ada(env)
    await env.docs.update(SYSTEM, "Employee", "ada", {**(await env.docs.get(SYSTEM, "Employee", "ada"))["data"], "audit_ref": "AUD-1"}, made["version"])
    seen = await env.docs.get(CLERK, "Employee", "ada")
    await env.docs.update(CLERK, "Employee", "ada", {**seen["data"], "department": "Atelier"}, seen["version"])
    kept = (await env.docs.get(SYSTEM, "Employee", "ada"))["data"]
    assert kept["department"] == "Atelier"
    assert (kept["salary"], kept["hr_notes"], kept["audit_ref"]) == ("5200.00", "À promouvoir", "AUD-1")
    assert kept["bonuses"] == [{"label": "Prime", "amount": "300.00"}]  # les lignes d'une table cachée, intactes
    assert env.docs.reveal_secret(SYSTEM, env.reg.get("Employee"), (await _stored(env, "ada")), "badge") == "s3cret"  # le secret, ni perdu ni re-scellé
    # Et l'historique de cette modification ne dit pas que les champs cachés ont bougé (ils n'ont pas bougé).
    [last, *_] = (await env.docs.history(HR, "Employee", "ada"))["items"]
    assert set(last["changes"]) == {"department"}


async def _stored(env, name):
    async with env.docs._query(SYSTEM) as q:
        from xcore_doctype.permissions import Scope

        return (await q.get("Employee", name, scope=Scope.everything("read"))).data


async def test_history_and_trash_show_only_what_the_caller_reads(env):
    made = await ada(env)
    full = (await env.docs.get(HR, "Employee", "ada"))["data"]
    await env.docs.update(HR, "Employee", "ada", {**full, "salary": "6000.00", "department": "Atelier"}, made["version"])
    await env.docs.update(HR, "Employee", "ada", {**(await env.docs.get(HR, "Employee", "ada"))["data"], "salary": "6100.00"}, made["version"] + 1)
    mine = (await env.docs.history(CLERK, "Employee", "ada"))["items"]
    assert not HIDDEN_FROM_CLERK & {k for item in mine for k in item["changes"]}
    assert "6000.00" not in repr(mine) and "5200.00" not in repr(mine) and "6100.00" not in repr(mine)
    assert [set(i["changes"]) for i in mine] == [set(), {"department"}, {"full_name", "department", "grade"}]  # l'entrée reste, vidée de ce qu'il ne lit pas
    theirs = (await env.docs.history(HR, "Employee", "ada"))["items"]
    assert theirs[0]["changes"]["salary"] == {"old": "6000.00", "new": "6100.00"}
    # Supprimé : la corbeille et l'historique du document disparu gardent TOUT le contenu — pas pour lui.
    await env.docs.delete(HR, "Employee", "ada")
    [trashed] = (await env.docs.trash(CLERK, "Employee"))["items"]
    assert not HIDDEN_FROM_CLERK & set(trashed["data"]) and "6100.00" not in repr(trashed)
    assert (await env.docs.trash(HR, "Employee"))["items"][0]["data"]["salary"] == "6100.00"
    gone = (await env.docs.history(CLERK, "Employee", "ada"))["items"]
    assert "6100.00" not in repr(gone) and not HIDDEN_FROM_CLERK & {k for item in gone for k in item["changes"]}


async def test_restoring_and_amending_bring_reserved_values_back_untouched(env):
    """Une restauration recopie ce qui était enregistré : ce n'est pas une saisie, les champs réservés reviennent sans qu'il les ait vus."""
    await ada(env)
    await env.docs.delete(HR, "Employee", "ada")
    [trashed] = (await env.docs.trash(CLERK, "Employee"))["items"]
    back = await env.docs.restore(CLERK, "Employee", trashed["id"])
    assert not HIDDEN_FROM_CLERK & set(back["data"])
    assert (await _stored(env, "ada"))["salary"] == "5200.00"


async def test_printing_is_reading_with_the_callers_fields(env):
    await ada(env)
    for_hr = (await env.printer.render(HR, "Employee", "ada"))["html"]
    for_clerk = (await env.printer.render(CLERK, "Employee", "ada"))["html"]
    assert "5\u00a0200,00" in for_hr  # le montant, écrit à la française
    for secret in ("5200", "5 200", "5\u00a0200", "À promouvoir", "Prime", "300", "s3cret"):
        assert secret not in for_clerk
    assert "Ada Lovelace" in for_clerk and "B2" in for_clerk  # ce qu'il lit s'imprime
    # Un gabarit qui CITE le champ (texte, tableau des lignes) : rendu avec les droits de qui imprime, la valeur manque, rien n'échoue.
    from xcore_doctype.printing import parse_template

    template = parse_template({"schema_version": 1, "doctype": "Employee", "name": "fiche", "blocks": [
        {"type": "text", "text": "Notes : {{ doc.hr_notes }} / {{ doc.salary }}"}, {"type": "table", "field": "bonuses", "columns": [{"field": "label"}, {"field": "amount"}]},
    ]})
    env.reg._print_templates[("Employee", "fiche")] = (template, "<test>", "hr")
    for_hr = (await env.printer.render(HR, "Employee", "ada", template="fiche"))["html"]
    for_clerk = (await env.printer.render(CLERK, "Employee", "ada", template="fiche"))["html"]
    assert "À promouvoir" in for_hr and "Prime" in for_hr and "300,00" in for_hr
    for secret in ("À promouvoir", "Prime", "300,00", "5\u00a0200"):
        assert secret not in for_clerk


async def test_a_file_of_a_reserved_field_is_not_served_listed_or_replaced(env):
    made = await ada(env)
    up = await env.files.upload(HR, "Employee", "contract", content=b"%PDF-1.4 contrat", filename="contrat.pdf", content_type="application/pdf")
    await env.docs.update(HR, "Employee", "ada", {**(await env.docs.get(HR, "Employee", "ada"))["data"], "contract": up["id"]}, made["version"])
    assert up["id"] in await env.files.links(HR, [up["id"]])
    assert await env.files.links(CLERK, [up["id"]]) == {}  # lire le document ne suffit pas : le fichier EST la valeur du champ
    assert [i["file_id"] for i in (await env.library.list(HR))["items"]] == [up["id"]]
    assert (await env.library.list(CLERK))["items"] == [] and (await env.library.list(CLERK))["total"] == 0
    with pytest.raises(DoctypeError) as e:
        await env.files.upload(CLERK, "Employee", "contract", content=b"%PDF-1.4 autre", filename="x.pdf", content_type="application/pdf")
    assert refused(e, "field_not_writable") == ["contract"]
    with pytest.raises(NotFound):  # ni détaché par l'écran « Fichiers » : pour lui, ce document ne cite rien
        await env.library.detach(CLERK, doctype="Employee", name="ada", field="contract", file_id=up["id"], version=made["version"] + 1)
    assert (await _stored(env, "ada"))["contract"] == up["id"]


async def test_connections_do_not_count_through_a_link_the_caller_cannot_read(env):
    await ada(env)
    await ada(env, name="bea", full_name="Bea", manager="ada")
    assert [(c["field"], c["count"]) for c in (await env.docs.connections(HR, "Employee", "ada"))["items"]] == [("manager", 1)]
    assert (await env.docs.connections(CLERK, "Employee", "ada"))["items"] == []  # « 1 fiche a Ada pour responsable » lirait le champ


async def test_meta_reflects_what_the_caller_may_do_and_never_names_a_grant(env):
    for_hr, for_clerk, for_pay = (env.docs.get_meta(c, "Employee") for c in (HR, CLERK, PAYROLL))
    assert SAL_R not in repr(for_hr) and "permissions" not in repr(for_hr["fields"])  # les règles ne sortent jamais
    assert {f["fieldname"]: f.get("access") for f in for_hr["fields"]} == {
        "full_name": None, "department": None, "salary": "write", "grade": "write", "hr_notes": "write", "manager": "write",
        "bonuses": "write", "badge": "write", "contract": "write",
    }  # `audit_ref` (`read: []`) n'existe pour personne ; un champ non réservé ne porte pas `access`
    # Le commis : les champs cachés n'existent pas — ni champ, ni place dans la fiche, ni lignes de la table.
    assert [f["fieldname"] for f in for_clerk["fields"]] == ["full_name", "department", "grade"]
    assert for_clerk["layout"][0]["columns"] == [["full_name", "department"], ["grade"]]
    assert not HIDDEN_FROM_CLERK & {n for s in for_clerk["layout"] for c in s["columns"] for n in c}
    assert not HIDDEN_FROM_CLERK & set(for_clerk["list_columns"]) and "children" not in for_clerk
    grade = next(f for f in for_clerk["fields"] if f["fieldname"] == "grade")
    assert (grade["access"], grade["read_only"]) == ("read", True)  # lu, pas écrit : le Desk le verrouille
    salary = next(f for f in for_pay["fields"] if f["fieldname"] == "salary")
    assert (salary["access"], salary["read_only"]) == ("read", True) and "children" in for_pay
    # Le tableau de bord fabriqué additionne les deux premières quantités : la carte « salaire (somme) » n'est pas servie à qui ne le lit pas.
    assert any(b.get("field") == "salary" for b in for_hr["views"]["dashboard"])
    assert not any(b.get("field") == "salary" for b in for_clerk["views"]["dashboard"])
    card = NumberCardBlock(type="number_card", label="Masse salariale", doctype="Employee", function="sum", field="salary")
    assert env.docs._visible_block(HR, card) is not None and env.docs._visible_block(CLERK, card) is None


def test_a_doctype_without_reserved_fields_keeps_its_meta_shape(env):
    meta = env.docs.get_meta(CLERK, "Team")
    assert all("access" not in f and "permissions" not in f for f in meta["fields"]) and [f["fieldname"] for f in meta["fields"]] == ["label", "budget"]


async def test_a_draft_made_from_another_document_copies_only_what_the_caller_reads(db, tables):
    """« Créer depuis » (`created_from`) recopie des valeurs dans un brouillon rendu à l'appelant : recopier, c'est montrer."""
    offer = {"schema_version": 1, "name": "Offer", "module": "hr", "naming": "prompt", "fields": [
        {"fieldname": "candidate", "fieldtype": "Text"}, {"fieldname": "salary", "fieldtype": "Currency", "permissions": {"read": [SAL_R]}}]}
    contract = {"schema_version": 1, "name": "Contract", "module": "hr", "created_from": [{"source": "Offer", "source_status": "draft"}], "fields": [
        {"fieldname": "candidate", "fieldtype": "Text"}, {"fieldname": "salary", "fieldtype": "Currency", "permissions": {"read": [SAL_R]}}]}
    reg = DocTypeRegistry()
    for raw in (offer, contract):
        reg.register(parse_definition(raw))
    reg.validate_links()
    docs = DocumentService(reg, db, tables, Log())
    grants = [f"hr:{t}:{a}" for t in ("offer", "contract") for a in ("read", "write")]
    hr, clerk = make_ctx("acme", "hana", permissions=[*grants, SAL_R]), make_ctx("acme", "carl", permissions=grants)
    await docs.create(hr, "Offer", {"candidate": "Ada", "salary": "5200.00"}, name="o1")
    assert (await docs.mapped_values(hr, "Contract", "Offer", "o1"))["values"] == {"candidate": "Ada", "salary": "5200.00"}
    assert (await docs.mapped_values(clerk, "Contract", "Offer", "o1"))["values"] == {"candidate": "Ada"}


async def test_the_form_builder_is_told_nothing_about_a_field_the_caller_cannot_read(env):
    seen = await env.docs.form_override(CLERK, "Employee")
    assert not HIDDEN_FROM_CLERK & (set(seen["labels"]) | set(seen["definition"]))
    assert {"salary", "grade"} <= set((await env.docs.form_override(HR, "Employee"))["labels"])


async def test_a_settings_document_follows_the_same_rules_before_and_after_its_first_save(db, tables):
    """Un document unique s'ouvre avant d'exister, rempli de ses défauts : celui d'un champ caché n'y est pas — le Desk le renverrait, et serait refusé."""
    settings = {"schema_version": 1, "name": "Pay Settings", "module": "hr", "single": True, "fields": [
        {"fieldname": "label", "fieldtype": "Text", "default": "Paie"},
        {"fieldname": "ceiling", "fieldtype": "Number", "default": 9000, "permissions": {"read": [SAL_R]}}]}
    reg = DocTypeRegistry()
    reg.register(parse_definition(settings))
    docs = DocumentService(reg, db, tables, Log())
    grants = ["hr:pay_settings:read", "hr:pay_settings:write"]
    hr, clerk = make_ctx("acme", "hana", permissions=[*grants, SAL_R]), make_ctx("acme", "carl", permissions=grants)
    opened = await docs.get(clerk, "Pay Settings", "Pay Settings")
    assert (opened["version"], opened["data"]) == (0, {"label": "Paie"})
    assert (await docs.get(hr, "Pay Settings", "Pay Settings"))["data"] == {"label": "Paie", "ceiling": 9000}
    saved = await docs.update(clerk, "Pay Settings", "Pay Settings", {**opened["data"], "label": "Paie 2026"}, 0)  # le premier enregistrement
    assert saved["data"] == {"label": "Paie 2026"}
    full = await docs.get(hr, "Pay Settings", "Pay Settings")
    assert full["data"]["ceiling"] == 9000  # le défaut du YAML, posé par le serveur
    await docs.update(hr, "Pay Settings", "Pay Settings", {**full["data"], "ceiling": 12000}, full["version"])
    again = await docs.get(clerk, "Pay Settings", "Pay Settings")
    await docs.update(clerk, "Pay Settings", "Pay Settings", {**again["data"], "label": "Paie"}, again["version"])
    assert (await docs.get(hr, "Pay Settings", "Pay Settings"))["data"] == {"label": "Paie", "ceiling": 12000}


# ── Écrire ────────────────────────────────────────────────────────────────────────────────────


async def test_a_hidden_field_is_refused_whatever_the_value_sent(env):
    """Pas d'oracle : la BONNE valeur est refusée comme la mauvaise, avec la même réponse. Sinon « salaire = 5200 ? » se sonderait par 200/403."""
    made = await ada(env)
    seen = (await env.docs.get(CLERK, "Employee", "ada"))["data"]
    answers = []
    for value in ("5200.00", "9999.00", None):
        with pytest.raises(DoctypeError) as e:
            await env.docs.update(CLERK, "Employee", "ada", {**seen, "salary": value}, made["version"])
        assert refused(e, "field_not_writable") == ["salary"]
        answers.append((e.value.status, e.value.code, e.value.message, e.value.details))
    assert answers[0] == answers[1] == answers[2]
    with pytest.raises(DoctypeError) as e:  # à la création aussi, et dans une table (ses lignes suivent le champ Table)
        await env.docs.create(CLERK, "Employee", {"full_name": "Zed", "salary": "1.00", "bonuses": [{"label": "x", "amount": "1.00"}], "audit_ref": "x"}, name="zed")
    assert refused(e, "field_not_writable") == ["audit_ref", "bonuses", "salary"]
    with pytest.raises(DoctypeError) as e:  # `[]` : même celle qui a tous les grants ne l'écrit pas
        await env.docs.create(HR, "Employee", {"full_name": "Zed", "audit_ref": "x"}, name="zed")
    assert refused(e, "field_not_writable") == ["audit_ref"]
    assert (await _stored(env, "ada"))["salary"] == "5200.00"


async def test_a_field_read_but_not_written_cannot_change(env):
    made = await ada(env)
    seen = (await env.docs.get(PAYROLL, "Employee", "ada"))["data"]
    assert seen["salary"] == "5200.00"
    for data in ({**seen, "salary": "9000.00"}, {k: v for k, v in seen.items() if k != "salary"}, {**seen, "grade": "C3"}):
        with pytest.raises(DoctypeError) as e:  # changé, OMIS (une mise à jour remplace : omettre, c'est vider), ou l'échelon
            await env.docs.update(PAYROLL, "Employee", "ada", data, made["version"])
        assert refused(e, "field_not_writable") in (["salary"], ["grade"])
    # Renvoyé tel quel (ce que fait le Desk) : accepté, et le reste s'écrit.
    done = await env.docs.update(PAYROLL, "Employee", "ada", {**seen, "department": "Atelier"}, made["version"])
    assert (done["data"]["salary"], done["data"]["department"]) == ("5200.00", "Atelier")
    # À la création : le défaut du YAML est posé par le serveur, l'envoyer tel quel passe ; une autre valeur, non.
    assert (await env.docs.create(CLERK, "Employee", {"full_name": "Cy"}, name="cy"))["data"]["grade"] == "A1"
    assert (await env.docs.create(CLERK, "Employee", {"full_name": "Di", "grade": "A1"}, name="di"))["data"]["grade"] == "A1"
    with pytest.raises(DoctypeError) as e:
        await env.docs.create(CLERK, "Employee", {"full_name": "Ed", "grade": "Z9"}, name="ed")
    assert refused(e, "field_not_writable") == ["grade"]
    # Qui a le grant l'écrit, et qui lit les notes les écrit (`write` absent suit `read`).
    full = await env.docs.get(HR, "Employee", "ada")
    assert (await env.docs.update(HR, "Employee", "ada", {**full["data"], "grade": "C3", "salary": "7000.00", "hr_notes": "Promue"}, full["version"]))["data"]["grade"] == "C3"


async def test_bulk_and_csv_import_are_not_a_way_round(env):
    await ada(env)
    await ada(env, name="bea", full_name="Bea")
    for data in ({"salary": "1.00"}, {"grade": "Z9"}):
        with pytest.raises(DoctypeError) as e:  # refusé UNE fois, d'emblée
            await env.docs.bulk(CLERK, "Employee", "update", ["ada", "bea"], data=data)
        assert refused(e, "field_not_writable") == list(data)
    report = await env.docs.bulk(CLERK, "Employee", "update", ["ada", "bea"], data={"department": "Atelier"})
    assert report["done"] == 2
    assert [(await _stored(env, n))["salary"] for n in ("ada", "bea")] == ["5200.00", "5200.00"]  # le lot ne vide pas ce qu'il ne voit pas
    for header in ("name;salary\nada;1.00\n", "name;grade\nada;Z9\n", "name;hr_notes\nada;x\n"):
        with pytest.raises(DoctypeError) as e:
            await import_csv(env.docs, CLERK, "Employee", header, "update")
        assert e.value.code == "field_not_writable"
    report = await import_csv(env.docs, CLERK, "Employee", "name;department\nada;Ventes\n", "update")
    assert report["done"] == 1 and (await _stored(env, "ada"))["salary"] == "5200.00"
    assert (await import_csv(env.docs, HR, "Employee", "name;salary\nada;6500.00\n", "update"))["done"] == 1
    assert (await _stored(env, "ada"))["salary"] == "6500.00"


async def test_an_organisation_rule_cannot_set_a_field_its_author_cannot_write(env):
    with pytest.raises(DoctypeError) as e:  # un défaut d'organisation écrirait l'échelon de tous, par procuration
        await env.docs.save_form_override(CLERK, "Employee", {"fields": {"grade": {"default": "Z9"}}})
    assert refused(e, "field_not_writable") == ["grade"]
    await env.docs.save_form_override(HR, "Employee", {"fields": {"grade": {"default": "B1"}}})
    await env.docs.save_form_override(CLERK, "Employee", {"fields": {"grade": {"default": "B1"}, "department": {"label": "Service"}}})  # un libellé : oui


# ── Déduire ───────────────────────────────────────────────────────────────────────────────────


async def test_filters_sorts_and_aggregates_on_a_hidden_field_fail_explicitly(env):
    await ada(env)
    await ada(env, name="bea", full_name="Bea", salary="100.00", bonuses=None)
    docs = env.docs
    doors = {
        "filtre": lambda c: docs.list(c, "Employee", filters={"salary": [">", "1000"]}),
        "filtre de ligne": lambda c: docs.list(c, "Employee", filters={"bonuses.amount": [">", "100"]}),
        "tri": lambda c: docs.list(c, "Employee", order_by="salary"),
        "somme": lambda c: docs.aggregate(c, "Employee", metrics=["sum:salary"]),
        "minimum": lambda c: docs.aggregate(c, "Employee", metrics=["count", "min:salary"]),
        "agrégat filtré": lambda c: docs.aggregate(c, "Employee", filters={"salary": [">", "1000"]}),
        "regroupement": lambda c: docs.aggregate(c, "Employee", group_by="manager"),
        "recherche filtrée": lambda c: docs.search(c, "Employee", "a", filters={"manager": "ada"}),
        "vue enregistrée": lambda c: docs.save_saved_view(c, "Employee", "riches", label="Riches", config={"filters": {"salary": "5200.00"}}),
        "vue triée": lambda c: docs.save_saved_view(c, "Employee", "tri", label="Tri", config={"order_by": "salary"}),
        "étiquettes": lambda c: env.collab.tag_counts(c, "Employee", filters={"salary": [">", "1000"]}),
    }
    for what, door in doors.items():
        with pytest.raises(DoctypeError) as e:
            await door(CLERK)
        assert refused(e, "field_not_readable")[0] in ("salary", "bonuses.amount", "manager"), what
        await door(HR)  # la même demande, pour qui lit le champ : servie
    # Le refus ne dépend pas des données : même réponse qu'il y ait 0 ou 2 documents au-dessus du seuil.
    assert [d["name"] for d in (await docs.list(HR, "Employee", filters={"salary": [">", "1000"]}))["items"]] == ["ada"]
    assert (await docs.aggregate(HR, "Employee", metrics=["sum:salary"]))["rows"][0]["values"] == ["5300.00"]
    # Ce qu'il lit se filtre et se trie toujours.
    assert (await docs.list(CLERK, "Employee", filters={"grade": "B2"}, order_by="full_name"))["total"] == 2
    assert (await docs.search(CLERK, "Employee", "ada"))["items"] == [{"name": "ada", "title": "Ada Lovelace"}]


async def test_the_sql_path_and_its_memory_twin_answer_alike(env):
    """Un type stocké et un type virtuel aux mêmes champs réservés : mêmes clés servies, mêmes refus, pour chaque jeu de grants."""
    for i, (name, salary) in enumerate((("Ada", "5200.00"), ("Bea", "100.00"))):
        data = {"full_name": name, "department": "Ventes", "salary": salary, "grade": "B2", "hr_notes": "n"}
        await env.docs.create(HR, "Employee", data, name=f"e{i}")
        await env.docs.create(HR, "EmployeeV", data)

    async def outcome(call):
        try:
            return await call
        except DoctypeError as e:
            return (e.status, e.code, sorted(d["field"] for d in e.details or []))

    def shape(answer):
        if isinstance(answer, tuple):
            return answer
        if "items" in answer:
            return sorted((tuple(sorted(i["data"])), i["data"].get("salary")) for i in answer["items"])
        return answer["rows"]

    for held in itertools.chain.from_iterable(itertools.combinations([SAL_R, SAL_W, GRADE_W, NOTES], n) for n in range(5)):
        ctx = make_ctx("acme", "u", permissions=[*BASE, *held])
        for door in (
            lambda dt: env.docs.list(ctx, dt),
            lambda dt: env.docs.list(ctx, dt, filters={"salary": [">", "1000"]}),
            lambda dt: env.docs.list(ctx, dt, order_by="hr_notes"),
            lambda dt: env.docs.aggregate(ctx, dt, metrics=["sum:salary"]),
            lambda dt: env.docs.aggregate(ctx, dt, group_by="department", filters={"hr_notes": "n"}),
        ):
            assert shape(await outcome(door("Employee"))) == shape(await outcome(door("EmployeeV"))), held
        # Écrire : la même règle chez la source.
        for dt, target in (("Employee", "e0"), ("EmployeeV", None)):
            target = target or sorted(env.source.docs["acme"])[0]
            seen = await env.docs.get(ctx, dt, target)
            verdict = await outcome(env.docs.update(ctx, dt, target, {**seen["data"], "salary": "1.00"}, seen["version"]))
            assert (verdict == (403, "field_not_writable", ["salary"])) == (SAL_W not in held or SAL_R not in held), (dt, held)
            if isinstance(verdict, dict):  # remis en place pour le tour suivant
                again = await env.docs.get(HR, dt, target)
                await env.docs.update(HR, dt, target, {**again["data"], "salary": "5200.00"}, again["version"])
    # Et chez la source non plus, enregistrer ne vide pas ce qu'on ne voit pas.
    target = sorted(env.source.docs["acme"])[0]
    seen = await env.docs.get(CLERK, "EmployeeV", target)
    await env.docs.update(CLERK, "EmployeeV", target, {**seen["data"], "department": "Atelier"}, seen["version"])
    assert env.source.docs["acme"][target].data["salary"] == "5200.00" and env.source.docs["acme"][target].data["hr_notes"] == "n"


async def test_a_notification_rule_cannot_carry_a_reserved_field(env):
    """Une règle diffuse à des destinataires dont le moteur ne connaît pas les grants : un champ réservé n'y entre par aucun bout."""
    base = {"document_type": "Employee", "event": "Création", "notify_owner": True}
    assert rule_problems(env.reg, {**base, "subject": "Bienvenue {{ doc.full_name }}"}) == []
    for extra in ({"subject": "{{ doc.salary }}"}, {"message": "Notes : {{ doc.hr_notes }}"}, {"condition": "hr_notes == 'x'"},
                  {"event": "Changement de valeur", "value_field": "salary"}, {"recipient_field": "manager"}):
        assert RESERVED_FIELD in repr(rule_problems(env.reg, {**base, **extra})), extra


# ── Entre organisations ───────────────────────────────────────────────────────────────────────


async def test_two_organisations_do_not_see_each_other_whatever_their_field_grants(env):
    await ada(env)
    await env.docs.create(OTHER_HR, "Employee", {"full_name": "Gus", "salary": "42.00"}, name="ada")  # le même NOM, ailleurs
    assert (await env.docs.get(OTHER_HR, "Employee", "ada"))["data"] == {"full_name": "Gus", "salary": "42.00", "grade": "A1"}
    assert (await env.docs.list(OTHER_HR, "Employee", filters={"salary": [">", "1000"]}))["total"] == 0  # les 5200 d'acme n'existent pas ici
    assert (await env.docs.aggregate(OTHER_HR, "Employee", metrics=["sum:salary"]))["rows"][0]["values"] == ["42.00"]
    assert [i["changes"]["salary"]["new"] for i in (await env.docs.history(OTHER_HR, "Employee", "ada"))["items"]] == ["42.00"]
    # Les grants de champ valent dans l'organisation du jeton : le commis d'acme ne gagne rien à ce que globex ait des RH.
    seen = await env.docs.get(CLERK, "Employee", "ada")
    assert "salary" not in seen["data"]
    await env.docs.update(CLERK, "Employee", "ada", {**seen["data"], "department": "Atelier"}, seen["version"])
    assert (await env.docs.get(OTHER_HR, "Employee", "ada"))["data"]["salary"] == "42.00"
    assert (await _stored(env, "ada"))["salary"] == "5200.00"


# ── Déclarer ──────────────────────────────────────────────────────────────────────────────────


def test_field_grants_are_declared_like_the_others(env):
    declared = {g["name"]: g for g in GrantCatalog(env.reg).for_plugin("hr")}
    assert {SAL_R, SAL_W, GRADE_W, NOTES} <= set(declared)
    assert declared[SAL_W]["description"] == "Fixer le salaire" and declared[SAL_R]["description"] == "Employee : lire « salary »"
    assert declared[SAL_R]["default_roles"] == ["tenant_admin"] and declared[SAL_R]["group"] == "hr-employee"


# ── Ce que la définition ne doit pas pouvoir révéler ──────────────────────────────────────────


def employee(fields=(), **extra):
    reserved = {"fieldname": "salary", "fieldtype": "Number", "permissions": {"read": [SAL_R]}}
    return {"schema_version": 1, "name": "E", "module": "hr", "fields": [{"fieldname": "label", "fieldtype": "Text"}, reserved, *fields], **extra}


@pytest.mark.parametrize(
    "raw,message",
    [
        (employee(title_field="secret", fields=[{"fieldname": "secret", "fieldtype": "Text", "permissions": {"read": [SAL_R]}}]), "title_field: secret est réservé"),
        (employee(naming={"field": "code"}, fields=[{"fieldname": "code", "fieldtype": "Text", "required": True, "permissions": {"read": [SAL_R]}}]), "naming: code est réservé"),
        (employee(naming={"template": "{label}-{code}"}, fields=[{"fieldname": "code", "fieldtype": "Text", "permissions": {"read": [SAL_R]}}]), "naming: code est réservé"),
        (employee(views={"kanban": {"field": "state"}}, fields=[{"fieldname": "state", "fieldtype": "Select", "options": ["a", "b"], "default": "a", "permissions": {"read": [SAL_R]}}]), "views.kanban: state est réservé"),
        (employee(views={"calendar": {"start": "day"}}, fields=[{"fieldname": "day", "fieldtype": "Date", "permissions": {"read": [SAL_R]}}]), "views.calendar: day est réservé"),
        (employee(workflow={"states": [{"name": "A"}, {"name": "B"}], "transitions": [{"action": "Go", "from": "A", "to": "B", "condition": "salary > 10"}]}),
         r"workflow.transitions\[Go\].condition: salary est réservé"),
        # Dériver : un champ qui lit un champ réservé doit l'être au moins autant.
        (employee(fields=[{"fieldname": "note", "fieldtype": "Text", "depends_on": "salary > 10"}]), "note.depends_on: lit 'salary'"),
        (employee(fields=[{"fieldname": "note", "fieldtype": "Text", "mandatory_depends_on": "salary > 10"}]), "note.mandatory_depends_on: lit 'salary'"),
        (employee(fields=[{"fieldname": "bonus", "fieldtype": "Number"}, {"fieldname": "total", "fieldtype": "Number", "compute": "salary + bonus"}]), "total.compute: lit 'salary'"),
        (employee(fields=[{"fieldname": "total", "fieldtype": "Number", "compute": "salary + salary", "permissions": {"read": [SAL_R, NOTES]}}]), "total.compute: lit 'salary'"),
        (employee(fields=[{"fieldname": "boss", "fieldtype": "Link", "options": "E", "permissions": {"read": [SAL_R]}},
                          {"fieldname": "boss_label", "fieldtype": "Text", "fetch_from": "boss.label"}]), "boss_label.fetch_from: lit 'boss'"),
        (employee(fields=[{"fieldname": "peer", "fieldtype": "Link", "options": "E", "link_filters": {"salary": "$salary"}}]), "peer.link_filters: lit 'salary'"),
        # La forme du bloc.
        (employee(fields=[{"fieldname": "x", "fieldtype": "Text", "permissions": {}}]), "donnez `read` et/ou `write`"),
        (employee(fields=[{"fieldname": "x", "fieldtype": "Text", "permissions": {"read": ["@authenticated"]}}]), "ne réserve rien"),
        (employee(fields=[{"fieldname": "x", "fieldtype": "Text", "permissions": {"read": ["role:admin"]}}]), "rôles ne sont pas pris en charge"),
        (employee(fields=[{"fieldname": "x", "fieldtype": "Text", "permissions": {"read": [SAL_R, SAL_R]}}]), "cité deux fois"),
        (employee(fields=[{"fieldname": "x", "fieldtype": "Text", "permissions": {"read": [{"grant": SAL_R, "if_owner": True}]}}]), "if_owner"),
        ({"schema_version": 1, "name": "L", "istable": True, "fields": [{"fieldname": "cost", "fieldtype": "Number", "permissions": {"read": [SAL_R]}}]},
         "pas pris en charge sur un champ de ligne"),
    ],
)
def test_a_definition_that_would_reveal_a_reserved_field_is_refused_at_load(raw, message):
    with pytest.raises(InvalidDefinition, match=message):
        parse_definition(raw)


def test_a_derived_field_at_least_as_reserved_loads():
    parse_definition(employee(fields=[
        {"fieldname": "bonus", "fieldtype": "Number"},
        {"fieldname": "total", "fieldtype": "Number", "compute": "salary + bonus", "permissions": {"read": [SAL_R]}},
        {"fieldname": "note", "fieldtype": "Text", "depends_on": "salary > 10", "permissions": {"read": [SAL_R], "write": [SAL_W]}},
    ]))


def test_what_another_doctype_would_reveal_is_refused_when_links_are_checked():
    def load(*raws):
        reg = DocTypeRegistry()
        for raw in raws:
            reg.register(parse_definition(raw))
        reg.validate_links()

    link = {"fieldname": "who", "fieldtype": "Link", "options": "E"}
    base = {"schema_version": 1, "name": "Card", "module": "hr"}
    with pytest.raises(InvalidDefinition, match="E.salary est réservé en lecture ; 'pay' doit l'être au moins autant"):
        load(employee(), {**base, "fields": [link, {"fieldname": "pay", "fieldtype": "Number", "fetch_from": "who.salary"}]})
    load(employee(), {**base, "fields": [link, {"fieldname": "pay", "fieldtype": "Number", "fetch_from": "who.salary", "permissions": {"read": [SAL_R]}}]})
    with pytest.raises(InvalidDefinition, match="E.salary est réservé en lecture .* un filtre de lien le révélerait"):
        load(employee(), {**base, "fields": [{**link, "link_filters": {"salary": 10}}]})
    with pytest.raises(InvalidDefinition, match="E.code est réservé en lecture .* le nom du document le publierait"):
        load(employee(fields=[{"fieldname": "code", "fieldtype": "Text", "permissions": {"read": [SAL_R]}}]),
             {**base, "naming": {"template": "{who.code}"}, "fields": [link]})
    # Une cellule dont la condition lit un champ réservé du PARENT : la table doit être au moins aussi réservée.
    row = {"schema_version": 1, "name": "Row", "istable": True, "fields": [{"fieldname": "why", "fieldtype": "Text", "depends_on": "parent_salary > 10"}]}
    with pytest.raises(InvalidDefinition, match="la table 'rows' doit l'être"):
        load(row, employee(fields=[{"fieldname": "rows", "fieldtype": "Table", "options": "Row"}]))
    load(row, employee(fields=[{"fieldname": "rows", "fieldtype": "Table", "options": "Row", "permissions": {"read": [SAL_R]}}]))


def test_an_organisation_cannot_add_a_transition_condition_on_a_reserved_field():
    raw = employee(workflow={"states": [{"name": "A"}, {"name": "B"}], "transitions": [{"action": "Go", "from": "A", "to": "B"}]})
    reg = DocTypeRegistry()
    compiled = reg.register(parse_definition(raw))
    with pytest.raises(DoctypeError, match="réservé en lecture"):
        validate_workflow_override(compiled, {"transitions": {"Go": {"condition": "salary > 10"}}})
    validate_workflow_override(compiled, {"transitions": {"Go": {"condition": "label == 'x'"}}})
