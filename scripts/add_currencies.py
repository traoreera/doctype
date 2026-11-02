"""T16b : la devise de chaque montant (`currency_from`) dans `doctypes/` — M3 de `process/plan-erp.md`.

Pourquoi une TABLE et pas une règle unique : une facture a deux devises (la sienne et celle de la société), une écriture
du grand livre en a quatre (société, compte, transaction, reporting), un paiement en a trois (compte de départ, compte
d'arrivée, société). Ce script applique donc, dans cet ordre :

1. `OVERRIDES` : les types dont chaque montant est nommé ici, à la main (grand livre, paiements, salaires…) ;
2. pour les autres, la STRUCTURE du type, lue sur sa définition et pas sur son nom :
   - un document qui porte `currency` : ses montants dans `currency`, ses montants `base_*` dans la devise de la
     société — la convention de ce jeu de types (hérité d'ERPNext) : `base_` est le montant converti en devise société ;
   - un document qui n'a que `company` : tout dans `company.default_currency` ;
   - une LIGNE : sa propre `currency` si elle en a une (une ligne de rapprochement est dans la devise de SA facture),
     sinon celle de son parent (`parent_currency`), à condition que TOUS ses parents en aient une — sinon le montant
     reste sans devise plutôt que d'en recevoir une fausse (un modèle de taxes n'a pas de devise, la facture si) ;
3. `NONE` : les types sans aucune source de devise, laissés tels quels et nommés (le test le vérifie).

Le moteur juge le résultat au CHARGEMENT (`registry._check_currencies`) : un champ cité qui n'existe pas, ou un parent
qui n'a pas la devise que sa ligne lui demande, fait échouer `tests/unit/test_erp_doctypes.py`. Idempotent : un champ
qui a déjà `currency_from` ou `options` n'est pas touché. `--check` n'écrit rien et dit ce qui changerait.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "doctypes"
COMPANY = "company.default_currency"

# 1. Les montants nommés un par un. `*` : tous les autres montants du type.
OVERRIDES: dict[str, dict[str, str]] = {
    # Le grand livre : débit/crédit en devise société, puis le même montant dans la devise du compte, de la transaction, du reporting.
    "GL Entry": {
        "debit": COMPANY, "credit": COMPANY,
        "debit_in_account_currency": "account_currency", "credit_in_account_currency": "account_currency",
        "debit_in_transaction_currency": "transaction_currency", "credit_in_transaction_currency": "transaction_currency",
        "debit_in_reporting_currency": "company.reporting_currency", "credit_in_reporting_currency": "company.reporting_currency",
    },
    "Account Closing Balance": {
        "debit": COMPANY, "credit": COMPANY,
        "debit_in_account_currency": "account_currency", "credit_in_account_currency": "account_currency",
        "debit_in_reporting_currency": "company.reporting_currency", "credit_in_reporting_currency": "company.reporting_currency",
    },
    "Payment Ledger Entry": {"amount": COMPANY, "amount_in_account_currency": "account_currency"},
    "Bank Reconciliation Tool": {"*": "account_currency"},
    "Journal Entry": {"total_amount": "total_amount_currency", "*": COMPANY},
    "Journal Entry Account": {
        "debit_in_account_currency": "account_currency", "credit_in_account_currency": "account_currency",
        "debit": "parent_" + COMPANY, "credit": "parent_" + COMPANY,
    },
    "Exchange Rate Revaluation Account": {
        "balance_in_account_currency": "account_currency", "new_balance_in_account_currency": "account_currency",
        "*": "parent_" + COMPANY,
    },
    # Un paiement : ce qui part du compte de départ, ce qui arrive sur le compte d'arrivée, le reste en devise société.
    "Payment Entry": {
        "paid_amount": "paid_from_account_currency", "paid_amount_after_tax": "paid_from_account_currency",
        "received_amount": "paid_to_account_currency", "received_amount_after_tax": "paid_to_account_currency",
        "*": COMPANY,
    },
    # Ce que doit le tiers est dans la devise de SON compte.
    "Sales Invoice": {"outstanding_amount": "party_account_currency"},
    "Purchase Invoice": {"outstanding_amount": "party_account_currency"},
    "POS Invoice": {"outstanding_amount": "party_account_currency"},
    "Sales Order": {"advance_paid": "party_account_currency"},
    "Purchase Order": {"advance_paid": "party_account_currency"},
    "Payment Request": {"outstanding_amount": "party_account_currency"},
    # Les salaires d'un employé sont dans SA devise de salaire.
    "Employee": {"*": "salary_currency"},
    "Employee Promotion": {"*": "salary_currency"},
    "Employee External Work History": {"*": "parent_salary_currency"},
    # La société elle-même.
    "Company": {"*": "default_currency"},
    # Une limite de crédit par société : la ligne cite SA société.
    "Customer Credit Limit": {"*": COMPANY},
    # La sous-traitance : le service se paie dans la devise du fournisseur, le reste est valorisé en devise société.
    "Subcontracting Order Service Item": {"base_rate": "parent_" + COMPANY, "base_amount": "parent_" + COMPANY, "*": "parent_supplier_currency"},
}

# 3. Aucune source : un référentiel sans société ni devise (un article, un poste de travail…), ou une ligne dont un parent
# n'en a pas (une équipe commerciale sert aussi la fiche client, une échéance de dépréciation un transfert d'actif).
NONE = {
    "Accounts Settings", "Activity Cost", "Activity Type", "Bank Guarantee", "Employee Benefit Ledger", "Employee Tax Exemption Category",
    "Employee Tax Exemption Sub Category", "Item", "Loan Repayment", "Overtime Type", "Quick Stock Balance", "Salary Component", "Shipment",
    "Vehicle Log", "Workstation", "Workstation Type",
    "Asset Finance Book", "BOM Operation", "Depreciation Schedule", "Process Payment Reconciliation Log Allocations", "Sales Team",
    "Shipment Delivery Note", "Vehicle Service", "Workstation Cost",
}
# Lignes dont SEULS les montants `base_*` reçoivent une devise : un de leurs parents (un modèle) n'a pas de `currency`.
BASE_ONLY = {"Sales Taxes and Charges", "Purchase Taxes and Charges", "Landed Cost Taxes and Charges"}


def is_base(fieldname: str) -> bool:
    return fieldname.startswith("base_")


def links(defn: dict) -> dict[str, str]:
    return {f["fieldname"]: f.get("options") for f in defn["fields"] if f.get("fieldtype") == "Link"}


def has_currency(defn: dict) -> bool:
    f = next((f for f in defn["fields"] if f["fieldname"] == "currency"), None)
    return f is not None and (f.get("fieldtype") in ("Text", "Select") or (f.get("fieldtype") == "Link" and f.get("options") == "Currency"))


def has_company(defn: dict) -> bool:
    return links(defn).get("company") == "Company"


def plan(docs: dict[str, dict]) -> dict[tuple[str, str], str | None]:
    """`(DocType, champ) -> source` pour CHAQUE montant sans devise ; None = laissé sans devise (nommé dans NONE / BASE_ONLY)."""
    parents: dict[str, list[dict]] = defaultdict(list)
    for d in docs.values():
        for f in d["fields"]:
            if f.get("fieldtype") == "Table":
                parents[f["options"]].append(d)
    out: dict[tuple[str, str], str | None] = {}
    for name, d in docs.items():
        amounts = [f["fieldname"] for f in d["fields"] if f.get("fieldtype") == "Currency" and not f.get("currency_from") and not f.get("options")]
        if not amounts:
            continue
        override = OVERRIDES.get(name, {})
        for fn in amounts:
            if fn in override or "*" in override:
                out[(name, fn)] = override.get(fn, override.get("*"))
                continue
            if name in NONE:
                out[(name, fn)] = None
                continue
            if not d.get("istable"):
                if has_currency(d) and not (is_base(fn) and has_company(d)):
                    out[(name, fn)] = "currency"
                elif has_company(d):
                    out[(name, fn)] = COMPANY
                else:
                    raise SystemExit(f"{name}.{fn} : aucune source de devise — à classer dans OVERRIDES ou NONE")
                continue
            ps = parents.get(name, [])
            all_company = bool(ps) and all(has_company(p) for p in ps)
            if is_base(fn):
                out[(name, fn)] = "parent_" + COMPANY if all_company else None
            elif name in BASE_ONLY:
                out[(name, fn)] = None
            elif has_currency(d):
                out[(name, fn)] = "currency"
            elif ps and all(has_currency(p) for p in ps):
                out[(name, fn)] = "parent_currency"
            elif all_company and not any(has_currency(p) for p in ps):
                out[(name, fn)] = "parent_" + COMPANY
            else:
                raise SystemExit(f"{name}.{fn} : parents {[p['name'] for p in ps]} sans devise commune — à classer")
            if out[(name, fn)] is None and name not in BASE_ONLY and not is_base(fn):
                raise SystemExit(f"{name}.{fn} : sans devise et non classé")
    return out


def insert(text: str, fieldname: str, source: str) -> str:
    """Pose `currency_from:` juste après `- fieldname: <champ>` (bloc) ; refuse une forme qu'il ne sait pas lire."""
    lines = text.split("\n")
    marker = f"- fieldname: {fieldname}"
    for i, line in enumerate(lines):
        if line.strip() == marker:
            indent = line[: len(line) - len(line.lstrip())] + "  "
            lines.insert(i + 1, f"{indent}currency_from: {source}")
            return "\n".join(lines)
        stripped = line.strip()
        if stripped.startswith("- {") and stripped.endswith("}") and f"fieldname: {fieldname}," in stripped:
            # Forme en ligne `- { fieldname: x, …, }` : la clé s'ajoute avant l'accolade fermante.
            cut = line.rstrip().rfind("}")
            lines[i] = line[:cut].rstrip() + f", currency_from: {source} " + line[cut:]
            return "\n".join(lines)
    raise SystemExit(f"champ {fieldname!r} introuvable (ni `- fieldname:` ni `- {{ fieldname: … }}`)")


def main(argv: list[str]) -> int:
    check = "--check" in argv
    files: dict[str, Path] = {}
    docs: dict[str, dict] = {}
    for path in sorted(ROOT.rglob("*.yaml")):
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        docs[d["name"]] = d
        files[d["name"]] = path
    wanted = plan(docs)
    counts = Counter(v or "(sans)" for v in wanted.values())
    by_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (doctype, fn), source in wanted.items():
        if source is not None:
            by_doc[doctype].append((fn, source))
    if not check:
        for doctype, items in by_doc.items():
            path = files[doctype]
            text = path.read_text(encoding="utf-8")
            for fn, source in items:
                text = insert(text, fn, source)
            path.write_text(text, encoding="utf-8")
            reread = {f["fieldname"]: f.get("currency_from") for f in yaml.safe_load(text)["fields"]}
            for fn, source in items:  # relu : la clé posée est bien celle du champ voulu
                assert reread.get(fn) == source, (doctype, fn, reread.get(fn), source)
    for source, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{n:5d}  {source}")
    print(f"{sum(1 for v in wanted.values() if v):5d}  montants qui {'recevraient' if check else 'reçoivent'} une devise, {len(by_doc)} types")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
