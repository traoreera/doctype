"""Le plan comptable SYSCOHADA révisé livré avec doctypes/ (`charts/syscohada.yaml`, décision du 2 nov. : la comptabilité de la première
version suit le système OHADA). Le plugin `accounts` (hors de ce dépôt) en crée les comptes d'une société.

Ce test garantit la COHÉRENCE du fichier — numéros, filiation, classes, nature bilan / résultat, valeurs que le type `Account` accepte —
pas son exactitude réglementaire, qu'un expert-comptable doit valider (le fichier le dit en tête).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from xcore_doctype.schema import parse_definition

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "syscohada.yaml"
BALANCE_ROOTS = {"Asset", "Liability", "Equity"}
RESULT_ROOTS = {"Income", "Expense"}

pytestmark = pytest.mark.skipif(not CHART.is_file(), reason="charts/syscohada.yaml absent")


@pytest.fixture(scope="module")
def accounts() -> list[dict]:
    return yaml.safe_load(CHART.read_text(encoding="utf-8"))["accounts"]


@pytest.fixture(scope="module")
def account_type():
    return parse_definition(yaml.safe_load((ROOT / "doctypes" / "accounts" / "account.yaml").read_text(encoding="utf-8")))


def test_numbers_are_unique_digits_and_the_nine_classes_are_the_roots(accounts):
    numbers = [a["number"] for a in accounts]
    assert len(numbers) == len(set(numbers))
    assert all(isinstance(n, str) and n.isdigit() for n in numbers)
    assert sorted(a["number"] for a in accounts if "parent" not in a) == [str(c) for c in range(1, 10)]
    assert all(a["name"].strip() for a in accounts)


def test_a_child_extends_its_parent_s_number_and_only_groups_have_children(accounts):
    by_number = {a["number"]: a for a in accounts}
    parents = {a["parent"] for a in accounts if "parent" in a}
    for a in accounts:
        if "parent" in a:
            parent = by_number.get(a["parent"])
            assert parent is not None, a
            assert a["number"].startswith(a["parent"]) and len(a["number"]) > len(a["parent"]), a
        assert bool(a.get("group")) == (a["number"] in parents), f"{a['number']} : un compte qui a des enfants est un regroupement, et seul lui"


def test_classes_1_to_5_are_the_balance_sheet_6_to_8_the_income_statement_9_neither(accounts):
    for a in accounts:
        cls = int(a["number"][0])
        report, root = a.get("report_type"), a.get("root_type")
        if cls == 9:
            assert report is None and root is None, a
            continue
        if root is None:  # une classe mixte (1, 4, 8) : la nature est portée par ses comptes
            assert a.get("group") and len(a["number"]) == 1, a
            continue
        assert report == ("Balance Sheet" if cls <= 5 else "Profit and Loss"), a
        assert root in (BALANCE_ROOTS if cls <= 5 else RESULT_ROOTS), a
    assert {a["root_type"] for a in accounts if a["number"].startswith("6") and "root_type" in a} == {"Expense"}
    assert {a["root_type"] for a in accounts if a["number"].startswith("7") and "root_type" in a} == {"Income"}


def test_a_child_has_its_parent_s_nature(accounts):
    by_number = {a["number"]: a for a in accounts}
    for a in accounts:
        parent = by_number.get(a.get("parent", ""))
        if parent is not None and parent.get("root_type"):
            assert a.get("root_type") == parent["root_type"], a


def test_every_value_is_one_the_account_type_accepts(accounts, account_type):
    fields = account_type.field_map
    for key in ("root_type", "report_type", "account_type"):
        allowed = set(fields[key].options)
        assert {a[key] for a in accounts if key in a} <= allowed, key


def test_the_key_accounts_an_erp_needs_are_there(accounts):
    """Ce que les écritures de vente, d'achat, de stock et de trésorerie citeront dès la première facture."""
    typed = {a["account_type"]: a["number"] for a in accounts if "account_type" in a}
    for kind in ("Receivable", "Payable", "Bank", "Cash", "Stock", "Tax", "Income Account", "Cost of Goods Sold"):
        assert kind in typed, kind
    assert (typed["Receivable"], typed["Payable"]) == ("411", "401")


def test_a_company_starts_from_the_syscohada_chart_and_every_account_is_numbered(account_type):
    company = parse_definition(yaml.safe_load((ROOT / "doctypes" / "setup" / "company.yaml").read_text(encoding="utf-8")))
    field = company.field_map["create_chart_of_accounts_based_on"]
    assert field.default == "SYSCOHADA révisé" and "SYSCOHADA révisé" in field.options
    assert account_type.field_map["account_number"].required
