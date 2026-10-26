"""Import CSV (programme groupe 2, élément 5).

Ce que ces tests défendent, chacun par une mutation qui le casse :
- chaque ligne passe par la porte ORDINAIRE (`create`/`update`) : droits, restrictions, schéma strict, liens, unicité, hooks ; une ligne refusée
  ne retient pas les autres et le rapport dit pour chacune ce qui s'est passé, avec son numéro de ligne dans le fichier ;
- l'en-tête est compris en entier ou refusé en entier (colonne inconnue, en double, champ qui ne s'importe pas) AVANT la première ligne ;
- chaque cellule devient la valeur que l'API attend (nombres et dates à la française compris), sans arrondi ni devinette ;
- le droit de créer (ou d'écrire) est exigé une fois d'abord ; une mise à jour garde ce que le fichier ne dit pas.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.csv_import import MAX_ROWS, NAME, SKIP, cell_value, import_csv, map_columns, read_csv
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DocumentValidationError, PermissionDenied, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import FieldDef, parse_definition

GRANTS = ["crm:client:read", "crm:client:write", "crm:pays:read", "crm:pays:write", "crm:code:read", "crm:code:write", "crm:reglage:read", "crm:reglage:write"]


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...
    def warning(self, *a, **k): ...


def _defs():
    return [
        {"schema_version": 1, "name": "Pays", "module": "crm", "fields": [{"fieldname": "nom", "fieldtype": "Text", "label": "Nom du pays"}]},
        {"schema_version": 1, "name": "Ligne", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Int"}]},
        {
            "schema_version": 1, "name": "Client", "module": "crm",
            "hooks": {"validate": ["crm.client"]},
            "fields": [
                {"fieldname": "nom", "fieldtype": "Text", "label": "Nom", "required": True},
                {"fieldname": "email", "fieldtype": "Text", "label": "Courriel", "unique": True},
                {"fieldname": "age", "fieldtype": "Int", "label": "Âge"},
                {"fieldname": "score", "fieldtype": "Number"},
                {"fieldname": "solde", "fieldtype": "Decimal", "decimals": 2, "label": "Solde"},
                {"fieldname": "actif", "fieldtype": "Boolean", "label": "Actif"},
                {"fieldname": "ne_le", "fieldtype": "Date", "label": "Né le"},
                {"fieldname": "vu", "fieldtype": "Datetime"},
                {"fieldname": "heure", "fieldtype": "Time"},
                {"fieldname": "statut", "fieldtype": "Select", "options": ["Prospect", "Client"], "default": "Prospect"},
                {"fieldname": "pays", "fieldtype": "Link", "options": "Pays", "label": "Pays"},
                {"fieldname": "pays_nom", "fieldtype": "Text", "fetch_from": "pays.nom"},
                {"fieldname": "secret", "fieldtype": "Password"},
                {"fieldname": "lignes", "fieldtype": "Table", "options": "Ligne"},
                {"fieldname": "notes", "fieldtype": "JSON"},
            ],
        },
        {"schema_version": 1, "name": "Code", "module": "crm", "naming": "prompt", "fields": [{"fieldname": "t", "fieldtype": "Text"}]},
        {"schema_version": 1, "name": "Reglage", "module": "crm", "single": True, "fields": [{"fieldname": "t", "fieldtype": "Text"}]},
    ]


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    for d in _defs():
        reg.register(parse_definition(d))
    reg.validate_links()
    hooks = HookRegistry()

    async def client_hook(event):
        if (event.data or {}).get("nom") == "Refusé":
            raise DocumentValidationError("Ce nom est refusé par le métier")

    hooks.register("crm.client", client_hook)
    return DocumentService(reg, db, tables, _Log(), hooks=hooks)


ADA = make_ctx("acme", "ada", permissions=GRANTS)
GLOBEX = make_ctx("globex", "gus", permissions=GRANTS)


def field(kind: str, **extra) -> FieldDef:
    return FieldDef(fieldname="f", fieldtype=kind, **extra)


class TestCells:
    @pytest.mark.parametrize(("kind", "extra", "text", "value"), [
        ("Int", {}, " 1 234 ", 1234),
        ("Int", {}, "-7", -7),
        ("Number", {}, "12", 12),
        ("Number", {}, "1 234,5", 1234.5),
        ("Number", {}, "0.25", 0.25),
        ("Percent", {}, "12,5", 12.5),
        ("Decimal", {"decimals": 2}, "1 234,5", "1234.50"),
        ("Currency", {"decimals": 2}, "10", "10.00"),
        ("Boolean", {}, "Oui", True),
        ("Boolean", {}, "faux", False),
        ("Boolean", {}, "1", True),
        ("Date", {}, "31/12/2026", "2026-12-31"),
        ("Date", {}, "2026-01-05", "2026-01-05"),
        ("Date", {}, "5/1/2026", "2026-01-05"),
        ("Datetime", {}, "2026-03-01T10:00:00+01:00", "2026-03-01T09:00:00Z"),
        ("Datetime", {}, "2026-03-01T10:00:00Z", "2026-03-01T10:00:00Z"),
        ("Time", {}, "9:05", "09:05:00"),
        ("Time", {}, "09:05:30", "09:05:30"),
        ("JSON", {}, '{"a": 1}', {"a": 1}),
        ("Color", {}, "#AABBCC", "#aabbcc"),
        ("Text", {}, "  bonjour  ", "bonjour"),
        ("Text", {}, "'=1+1", "=1+1"),  # l'apostrophe que l'export pose devant une formule est retirée au retour
        ("Text", {}, "'bonjour", "'bonjour"),  # mais pas devant un texte ordinaire
        ("Duration", {}, "5400", 5400),
        ("Duration", {}, "1:30:00", 5400),
        ("Duration", {}, "0:05", 300),
    ])
    def test_a_cell_becomes_the_api_value(self, kind, extra, text, value):
        assert cell_value(field(kind, **extra), text) == value

    @pytest.mark.parametrize(("kind", "extra", "text"), [
        ("Int", {}, "1.5"),
        ("Int", {}, "abc"),
        ("Number", {}, "1,234.5"),  # virgule ET point : ambigu, jamais deviné
        ("Number", {}, "nan"),
        ("Number", {}, "inf"),
        ("Number", {}, "1e400"),
        ("Number", {}, "9" * 400 + ".5"),  # infini une fois lu : refusé (un NaN/infini stocké casserait la liste)
        ("Decimal", {"decimals": 2}, "1,234"),  # trois décimales pour un champ à deux : refusé, pas arrondi
        ("Boolean", {}, "peut-être"),
        ("Date", {}, "31/02/2026"),
        ("Date", {}, "2026-13-01"),
        ("Datetime", {}, "2026-03-01T10:00:00"),  # sans fuseau : l'heure de qui ?
        ("Time", {}, "midi"),
        ("Duration", {}, "1h"),
        ("JSON", {}, "{a"),
    ])
    def test_an_unreadable_cell_is_refused(self, kind, extra, text):
        with pytest.raises(ValueError):
            cell_value(field(kind, **extra), text)


    def test_a_comma_and_a_dot_together_are_called_ambiguous(self):
        with pytest.raises(ValueError, match="ambigu"):
            cell_value(field("Number"), "1,234.5")


class TestFile:
    def test_semicolon_tab_and_comma_are_recognised_from_the_header(self):
        assert read_csv("a;b\n1;2\n") == (["a", "b"], [["1", "2"]])
        assert read_csv("a\tb\n1\t2\n") == (["a", "b"], [["1", "2"]])
        assert read_csv('a,b\n"1,5",2\n') == (["a", "b"], [["1,5", "2"]])

    def test_bom_blank_lines_and_crlf_are_ignored(self):
        assert read_csv("﻿a;b\r\n\r\n1;2\r\n;\r\n") == (["a", "b"], [["1", "2"]])

    def test_a_quoted_cell_keeps_its_line_breaks(self):
        assert read_csv('a;b\n"x\ny";2\n')[1] == [["x\ny", "2"]]

    @pytest.mark.parametrize(("content", "code"), [("", "csv_empty"), ("a;b\n", "csv_empty"), (None, "csv_invalid"), (12, "csv_invalid")])
    def test_nothing_to_import_is_refused(self, content, code):
        with pytest.raises(ValidationFailed) as e:
            read_csv(content)
        assert e.value.code == code

    def test_limits(self):
        with pytest.raises(ValidationFailed) as e:
            read_csv("a\n" + "x\n" * (MAX_ROWS + 1))
        assert e.value.code == "csv_too_large"
        read_csv("a\n" + "x\n" * MAX_ROWS)
        with pytest.raises(ValidationFailed) as e:
            read_csv("a\n" + "x" * 2_000_001)
        assert e.value.code == "csv_too_large"


class TestColumns:
    def definition(self, svc, name="Client"):
        return svc._doctype(name).definition

    def test_a_column_is_a_fieldname_or_a_label_whatever_its_case(self, svc):
        columns = map_columns(self.definition(svc), ["nom", "COURRIEL", "âge", "Né le"], "insert")
        assert [c.fieldname for c in columns] == ["nom", "email", "age", "ne_le"]

    @pytest.mark.parametrize("header", [["nom", "inconnu"], ["Courriel", "email"], ["nom", "secret"], ["nom", "lignes"], ["nom", "pays_nom"], ["nom", ""]])
    def test_a_header_that_is_not_fully_understood_refuses_the_whole_import(self, svc, header):
        with pytest.raises(ValidationFailed) as e:
            map_columns(self.definition(svc), header, "insert")
        assert e.value.code == "csv_columns" and e.value.details[0]["field"].startswith("colonne 2")

    def test_a_label_shared_by_two_fields_must_be_written_as_a_fieldname(self):
        twins = parse_definition({"schema_version": 1, "name": "T", "module": "crm", "fields": [
            {"fieldname": "a", "fieldtype": "Text", "label": "Code"}, {"fieldname": "b", "fieldtype": "Text", "label": "code"}]})
        with pytest.raises(ValidationFailed) as e:
            map_columns(twins, ["Code"], "insert")
        assert "plusieurs champs" in e.value.details[0]["msg"]
        assert [c.fieldname for c in map_columns(twins, ["b", "a"], "insert")] == ["b", "a"]

    def test_the_name_column(self, svc):
        with pytest.raises(ValidationFailed):
            map_columns(self.definition(svc), ["name", "nom"], "insert")  # ce type nomme lui-même
        with pytest.raises(ValidationFailed):
            map_columns(self.definition(svc), ["nom"], "update")  # mettre à jour quoi ?
        assert map_columns(self.definition(svc), ["Name", "nom"], "update")[0] == NAME
        assert map_columns(self.definition(svc, "Code"), ["name", "t"], "insert")[0] == NAME

    def test_a_desk_export_reads_back_name_first_system_columns_ignored(self, svc):
        # L'export du Desk écrit le nom du document sous « Nom », puis les champs par libellé ; ici un champ s'appelle AUSSI « Nom ».
        columns = map_columns(self.definition(svc), ["Nom", "Nom", "Propriétaire", "Création", "Modification", "Statut", "Âge"], "update")
        assert columns[0] == NAME and columns[1].fieldname == "nom" and columns[2:6] == [SKIP] * 4 and columns[6].fieldname == "age"
        assert map_columns(self.definition(svc), ["Nom", "Âge"], "insert")[0].fieldname == "nom"  # un champ passe avant le nom du document
        assert map_columns(self.definition(svc, "Code"), ["Nom", "owner", "t"], "insert")[:2] == [NAME, SKIP]  # aucun champ « Nom » ici


class TestImport:
    async def test_each_row_is_created_through_the_ordinary_door_and_reported(self, svc):
        pays = await svc.create(ADA, "Pays", {"nom": "France"})
        content = (
            "Nom;Courriel;Âge;Solde;Actif;Né le;Pays\n"
            f"Ada;ada@x.org;36;1 234,5;oui;10/12/1815;{pays['name']}\n"
            "Bob;bob@x.org;douze;;;;\n"  # cellule illisible : ligne refusée sans appeler le service
            f"Cyd;ada@x.org;;;;;\n"  # unicité : refusée par le service
            ";dan@x.org;;;;;\n"  # nom obligatoire manquant
            "Eve;eve@x.org;;;;;inconnu\n"  # lien vers rien
            "Refusé;f@x.org;;;;;\n"  # hook
            "Fay;fay@x.org;;;non;;\n"
        )
        report = await import_csv(svc, ADA, "Client", content)
        rows = {r["row"]: r for r in report["results"]}
        assert (report["done"], report["failed"]) == (2, 5)
        assert rows[2]["ok"] and rows[8]["ok"]
        assert rows[3]["code"] == "invalid_value" and rows[3]["details"][0]["field"] == "age"
        assert [(rows[n]["code"], rows[n]["details"][0]["field"], rows[n]["details"][0]["type"]) for n in (4, 5, 6)] == [
            ("validation_failed", "email", "unique"), ("validation_failed", "nom", "missing"), ("validation_failed", "pays", "link_not_found")]
        assert rows[7]["code"] == "hook_rejected" and "refusé" in rows[7]["message"]
        ada = await svc.get(ADA, "Client", rows[2]["name"])
        assert {k: ada["data"][k] for k in ("nom", "email", "age", "solde", "actif", "ne_le", "pays", "pays_nom", "statut")} == {
            "nom": "Ada", "email": "ada@x.org", "age": 36, "solde": "1234.50", "actif": True, "ne_le": "1815-12-10", "pays": pays["name"],
            "pays_nom": "France", "statut": "Prospect",  # fetch_from recalculé, défaut appliqué : la porte ordinaire
        }
        assert ada["owner"] == "ada"

    async def test_update_keeps_what_the_file_does_not_say(self, svc):
        made = await svc.create(ADA, "Client", {"nom": "Ada", "email": "a@x.org", "age": 36, "statut": "Client"})
        report = await import_csv(svc, ADA, "Client", f"name;Âge;Courriel\n{made['name']};37;  \nabsent;1;\n", "update")  # blanc = vide
        assert [r["ok"] for r in report["results"]] == [True, False]
        assert report["results"][1]["code"] == "document_not_found"
        after = await svc.get(ADA, "Client", made["name"])
        assert (after["data"]["age"], after["data"]["email"], after["data"]["statut"], after["version"]) == (37, "a@x.org", "Client", made["version"] + 1)

    async def test_a_chosen_name_for_a_prompt_doctype(self, svc):
        report = await import_csv(svc, ADA, "Code", "name;t\nABC;x\n;y\n")
        assert report["results"][0] == {"row": 2, "ok": True, "name": "ABC"}
        assert not report["results"][1]["ok"]  # le nom est obligatoire pour ce type

    async def test_the_right_is_required_once_before_anything(self, svc):
        reader = make_ctx("acme", "rita", permissions=["crm:client:read"])
        with pytest.raises(PermissionDenied):
            await import_csv(svc, reader, "Client", "Nom\nA\n")
        with pytest.raises(PermissionDenied):
            await import_csv(svc, reader, "Client", "name;Nom\nx;A\n", "update")
        writer_no_create = make_ctx("acme", "wes", permissions=["crm:client:read", "crm:client:write"])
        assert (await import_csv(svc, writer_no_create, "Client", "Nom\nA\n"))["done"] == 1  # write = créer/modifier (convention)

    async def test_rows_land_in_the_callers_tenant_only(self, svc):
        await import_csv(svc, ADA, "Client", "Nom\nA\nB\n")
        assert len((await svc.list(ADA, "Client"))["items"]) == 2
        assert not (await svc.list(GLOBEX, "Client"))["items"]

    async def test_an_update_cannot_reach_another_tenants_document(self, svc):
        theirs = await svc.create(GLOBEX, "Client", {"nom": "G"})
        report = await import_csv(svc, ADA, "Client", f"name;Nom\n{theirs['name']};volé\n", "update")
        assert report["results"][0]["code"] == "document_not_found"
        assert (await svc.get(GLOBEX, "Client", theirs["name"]))["data"]["nom"] == "G"

    async def test_bad_mode_single_doctype_and_header_errors_refuse_before_any_row(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await import_csv(svc, ADA, "Client", "Nom\nA\n", "upsert")
        assert e.value.code == "csv_mode"
        with pytest.raises(ValidationFailed) as e:
            await import_csv(svc, ADA, "Reglage", "t\nA\n")
        assert e.value.code == "single_document"
        with pytest.raises(ValidationFailed):
            await import_csv(svc, ADA, "Client", "Nom;inconnu\nA;1\n")
        assert not (await svc.list(ADA, "Client"))["items"]

    async def test_a_row_longer_than_the_header_is_refused(self, svc):
        report = await import_csv(svc, ADA, "Client", "Nom;Âge\nA;1;2\nC;3\n")
        assert [r["ok"] for r in report["results"]] == [False, True]
        assert report["results"][0]["details"][0]["type"] == "csv_row"
