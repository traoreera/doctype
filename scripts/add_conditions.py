"""Migration T16 : les formulaires de l'ERP s'allègent et se remplissent seuls (`depends_on`, `fetch_from`).

Mesuré avant d'écrire (29 oct.) : quatre fichiers seulement sur 704 portaient une condition ou une copie. Une
facture montrait donc les 158 champs de sa définition, ceux du point de vente comme ceux de l'avoir, et le nom du
client ne s'y recopiait pas.

Deux tables écrites à la main, comme T15 (`add_computes.py`), et aucune expression reprise d'ERPNext (GPL-3.0) :

- **`FETCHES`** — les copies d'IDENTITÉ d'un référentiel (le nom du client, du fournisseur, de l'employé, de
  l'article, du projet, de la piste) : le champ voisin d'un Link dont la cible porte le même champ, de type
  compatible (`registry.fetch_incompatible`). Sélection : le champ était déjà en lecture seule, OU son lien est
  obligatoire — parce qu'avec un lien vide le serveur VIDE la copie (`DocumentService._fetched`) : sur une ligne
  où l'article est facultatif, recopier `item_name` effacerait un libellé saisi à la main. Le `required` d'un
  champ devenu copié est retiré (le schéma refuse les deux ensemble ; le lien obligatoire garantit la valeur).
  Les autres candidats de la même règle (729 en lecture seule) ont été écartés à la relecture : un `grand_total`
  ou un `status` voisin d'un lien n'est pas une copie, c'est un homonyme.
- **`CONDITIONS`** — `depends_on` / `mandatory_depends_on` écrits pièce par pièce depuis le sens métier de chaque
  champ (l'avoir cite sa facture d'origine, le point de vente a son profil, un salarié parti a sa date de sortie).
  Jamais sur un Decimal, une Table, un secret : le schéma les refuse dans une condition.

    poetry run python scripts/add_conditions.py            # rapport seul
    poetry run python scripts/add_conditions.py --write     # applique
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent / "doctypes"

# Fichier -> {champ: lien.champ_source}.
FETCHES: dict[str, dict[str, str]] = {
    "accounts/dunning.yaml": {"customer_name": "customer.customer_name"},
    "accounts/pos_invoice.yaml": {"customer_name": "customer.customer_name"},
    "accounts/process_statement_of_accounts_customer.yaml": {"customer_name": "customer.customer_name"},
    "accounts/purchase_invoice.yaml": {"supplier_name": "supplier.supplier_name"},
    "assets/asset.yaml": {"item_name": "item_code.item_name"},
    "assets/asset_capitalization_asset_item.yaml": {"item_name": "item_code.item_name"},
    "assets/asset_capitalization_service_item.yaml": {"item_name": "item_code.item_name"},
    "assets/asset_capitalization_stock_item.yaml": {"item_name": "item_code.item_name"},
    "buying/purchase_order.yaml": {"customer_name": "customer.customer_name", "supplier_name": "supplier.supplier_name"},
    "buying/purchase_order_item.yaml": {"item_name": "item_code.item_name"},
    "buying/request_for_quotation_item.yaml": {"item_name": "item_code.item_name"},
    "buying/request_for_quotation_supplier.yaml": {"supplier_name": "supplier.supplier_name"},
    "buying/supplier_quotation.yaml": {"supplier_name": "supplier.supplier_name"},
    "buying/supplier_quotation_item.yaml": {"item_name": "item_code.item_name"},
    "crm/prospect_lead.yaml": {"lead_name": "lead.lead_name"},
    "hr/appraisal.yaml": {"employee_name": "employee.employee_name"},
    "hr/appraisee.yaml": {"employee_name": "employee.employee_name"},
    "hr/attendance.yaml": {"employee_name": "employee.employee_name"},
    "hr/attendance_request.yaml": {"employee_name": "employee.employee_name"},
    "hr/compensatory_leave_request.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_advance.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_checkin.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_performance_feedback.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_promotion.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_separation.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_skill_map.yaml": {"employee_name": "employee.employee_name"},
    "hr/employee_transfer.yaml": {"employee_name": "employee.employee_name"},
    "hr/exit_interview.yaml": {"employee_name": "employee.employee_name"},
    "hr/expense_claim.yaml": {"employee_name": "employee.employee_name"},
    "hr/full_and_final_statement.yaml": {"employee_name": "employee.employee_name"},
    "hr/goal.yaml": {"employee_name": "employee.employee_name"},
    "hr/leave_adjustment.yaml": {"employee_name": "employee.employee_name"},
    "hr/leave_allocation.yaml": {"employee_name": "employee.employee_name"},
    "hr/leave_application.yaml": {"employee_name": "employee.employee_name"},
    "hr/leave_encashment.yaml": {"employee_name": "employee.employee_name"},
    "hr/leave_policy_assignment.yaml": {"employee_name": "employee.employee_name"},
    "hr/overtime_slip.yaml": {"employee_name": "employee.employee_name"},
    "hr/shift_assignment.yaml": {"employee_name": "employee.employee_name"},
    "hr/shift_request.yaml": {"employee_name": "employee.employee_name"},
    "hr/shift_schedule_assignment.yaml": {"employee_name": "employee.employee_name"},
    "hr/training_event_employee.yaml": {"employee_name": "employee.employee_name"},
    "hr/training_feedback.yaml": {"employee_name": "employee.employee_name"},
    "hr/training_result_employee.yaml": {"employee_name": "employee.employee_name"},
    "hr/travel_request.yaml": {"employee_name": "employee.employee_name"},
    "maintenance/maintenance_schedule.yaml": {"customer_name": "customer.customer_name"},
    "maintenance/maintenance_schedule_detail.yaml": {"item_name": "item_code.item_name"},
    "maintenance/maintenance_schedule_item.yaml": {"item_name": "item_code.item_name"},
    "maintenance/maintenance_visit.yaml": {"customer_name": "customer.customer_name"},
    "maintenance/maintenance_visit_purpose.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/blanket_order.yaml": {"customer_name": "customer.customer_name", "supplier_name": "supplier.supplier_name"},
    "manufacturing/blanket_order_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/bom.yaml": {"item_name": "item.item_name"},
    "manufacturing/bom_creator.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/bom_creator_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/bom_explosion_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/bom_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/bom_secondary_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/job_card_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/job_card_secondary_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/master_production_schedule_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/material_request_plan_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/production_plan_schedule.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/sales_forecast_item.yaml": {"item_name": "item_code.item_name"},
    "manufacturing/work_order_item.yaml": {"item_name": "item_code.item_name"},
    "payroll/additional_salary.yaml": {"employee_name": "employee.employee_name"},
    "payroll/arrear.yaml": {"employee_name": "employee.employee_name"},
    "payroll/employee_benefit_application.yaml": {"employee_name": "employee.employee_name"},
    "payroll/employee_benefit_claim.yaml": {"employee_name": "employee.employee_name"},
    "payroll/employee_incentive.yaml": {"employee_name": "employee.employee_name"},
    "payroll/employee_other_income.yaml": {"employee_name": "employee.employee_name"},
    "payroll/employee_tax_exemption_declaration.yaml": {"employee_name": "employee.employee_name"},
    "payroll/employee_tax_exemption_proof_submission.yaml": {"employee_name": "employee.employee_name"},
    "payroll/gratuity.yaml": {"employee_name": "employee.employee_name"},
    "payroll/payroll_correction.yaml": {"employee_name": "employee.employee_name"},
    "payroll/payroll_employee_detail.yaml": {"employee_name": "employee.employee_name"},
    "payroll/retention_bonus.yaml": {"employee_name": "employee.employee_name"},
    "payroll/salary_slip.yaml": {"employee_name": "employee.employee_name"},
    "payroll/salary_structure_assignment.yaml": {"employee_name": "employee.employee_name"},
    "payroll/salary_withholding.yaml": {"employee_name": "employee.employee_name"},
    "projects/activity_cost.yaml": {"employee_name": "employee.employee_name"},
    "projects/timesheet.yaml": {"employee_name": "employee.employee_name"},
    "projects/timesheet_detail.yaml": {"project_name": "project.project_name"},
    "selling/installation_note.yaml": {"customer_name": "customer.customer_name"},
    "selling/proforma_invoice.yaml": {"customer_name": "customer.customer_name"},
    "selling/proforma_invoice_item.yaml": {"item_name": "item_code.item_name"},
    "selling/sales_order.yaml": {"customer_name": "customer.customer_name"},
    "selling/sales_order_item.yaml": {"item_name": "item_code.item_name"},
    "stock/delivery_note.yaml": {"customer_name": "customer.customer_name"},
    "stock/delivery_note_item.yaml": {"item_name": "item_code.item_name"},
    "stock/item_alternative.yaml": {"item_name": "item_code.item_name"},
    "stock/item_lead_time.yaml": {"item_name": "item_code.item_name"},
    "stock/item_manufacturer.yaml": {"item_name": "item_code.item_name"},
    "stock/item_price.yaml": {"item_name": "item_code.item_name"},
    "stock/material_request_item.yaml": {"item_name": "item_code.item_name"},
    "stock/packed_item.yaml": {"item_name": "item_code.item_name"},
    "stock/packing_slip_item.yaml": {"item_name": "item_code.item_name"},
    "stock/pick_list.yaml": {"customer_name": "customer.customer_name"},
    "stock/pick_list_item.yaml": {"item_name": "item_code.item_name"},
    "stock/purchase_receipt.yaml": {"supplier_name": "supplier.supplier_name"},
    "stock/purchase_receipt_item.yaml": {"item_name": "item_code.item_name"},
    "stock/putaway_rule.yaml": {"item_name": "item_code.item_name"},
    "stock/quality_inspection.yaml": {"item_name": "item_code.item_name"},
    "stock/quick_stock_balance.yaml": {"item_name": "item.item_name"},
    "stock/serial_and_batch_bundle.yaml": {"item_name": "item_code.item_name"},
    "stock/serial_no.yaml": {"item_name": "item_code.item_name"},
    "stock/stock_closing_balance.yaml": {"item_name": "item_code.item_name"},
    "stock/stock_entry.yaml": {"supplier_name": "supplier.supplier_name"},
    "stock/stock_entry_detail.yaml": {"item_name": "item_code.item_name"},
    "stock/stock_reconciliation_item.yaml": {"item_name": "item_code.item_name"},
    "subcontracting/subcontracting_inward_order.yaml": {"customer_name": "customer.customer_name"},
    "subcontracting/subcontracting_inward_order_item.yaml": {"item_name": "item_code.item_name"},
    "subcontracting/subcontracting_inward_order_service_item.yaml": {"item_name": "item_code.item_name"},
    "subcontracting/subcontracting_order.yaml": {"supplier_name": "supplier.supplier_name"},
    "subcontracting/subcontracting_order_item.yaml": {"item_name": "item_code.item_name"},
    "subcontracting/subcontracting_order_service_item.yaml": {"item_name": "item_code.item_name"},
    "subcontracting/subcontracting_receipt.yaml": {"supplier_name": "supplier.supplier_name"},
    "subcontracting/subcontracting_receipt_item.yaml": {"item_name": "item_code.item_name"},
    "support/issue.yaml": {"customer_name": "customer.customer_name"},
    "support/warranty_claim.yaml": {"customer_name": "customer.customer_name", "item_name": "item_code.item_name"},
    # Le nom du client de la facture de vente (voir RETYPE).
    "accounts/sales_invoice.yaml": {"customer_name": "customer.customer_name"},
}

# Les changements de type qu'une copie exige (fichier -> {champ: nouveau type}). Aucune donnée n'existe encore.
# Le nom du client de la facture de vente était un LongText, alors que `Customer.customer_name` est un Text : la
# copie aurait été refusée au chargement.
RETYPE: dict[str, dict[str, str]] = {
    "accounts/sales_invoice.yaml": {"customer_name": "Text"},
}

POS = "is_pos"
LEFT = "status == 'Left'"
NOT_INTERNAL = "payment_type != 'Internal Transfer'"
DEFERRED_IN = "enable_deferred_revenue"
DEFERRED_OUT = "enable_deferred_expense"
# Fichier -> {champ: {"depends_on": …, "mandatory_depends_on": …}}.
CONDITIONS: dict[str, dict[str, dict[str, str]]] = {
    # ── La facture de vente : point de vente, avoir, client interne, stock ──
    "accounts/sales_invoice.yaml": {
        "pos_profile": {"depends_on": POS, "mandatory_depends_on": POS},
        "cash_bank_account": {"depends_on": POS},
        "account_for_change_amount": {"depends_on": POS},
        "return_against": {"depends_on": "is_return", "mandatory_depends_on": "is_return"},
        "loyalty_program": {"depends_on": "redeem_loyalty_points"},
        "loyalty_points": {"depends_on": "redeem_loyalty_points"},
        "loyalty_redemption_account": {"depends_on": "redeem_loyalty_points"},
        "loyalty_redemption_cost_center": {"depends_on": "redeem_loyalty_points"},
        "represents_company": {"depends_on": "is_internal_customer", "mandatory_depends_on": "is_internal_customer"},
        "inter_company_invoice_reference": {"depends_on": "is_internal_customer"},
        "unrealized_profit_loss_account": {"depends_on": "is_internal_customer"},
        "set_target_warehouse": {"depends_on": "is_internal_customer"},
        "set_warehouse": {"depends_on": "update_stock"},
        "po_date": {"depends_on": "po_no"},
    },
    "accounts/sales_invoice_item.yaml": {
        "deferred_revenue_account": {"depends_on": DEFERRED_IN, "mandatory_depends_on": DEFERRED_IN},
        "service_start_date": {"depends_on": DEFERRED_IN, "mandatory_depends_on": DEFERRED_IN},
        "service_end_date": {"depends_on": DEFERRED_IN, "mandatory_depends_on": DEFERRED_IN},
        "service_stop_date": {"depends_on": DEFERRED_IN},
        "asset": {"depends_on": "is_fixed_asset"},
    },
    # ── La facture d'achat : son miroir, plus la mise en attente et le paiement immédiat ──
    "accounts/purchase_invoice.yaml": {
        "return_against": {"depends_on": "is_return", "mandatory_depends_on": "is_return"},
        "release_date": {"depends_on": "on_hold"},
        "hold_comment": {"depends_on": "on_hold"},
        "mode_of_payment": {"depends_on": "is_paid"},
        "cash_bank_account": {"depends_on": "is_paid", "mandatory_depends_on": "is_paid"},
        "set_warehouse": {"depends_on": "update_stock"},
        "rejected_warehouse": {"depends_on": "update_stock"},
        "supplier_warehouse": {"depends_on": "is_subcontracted"},
        "represents_company": {"depends_on": "is_internal_supplier", "mandatory_depends_on": "is_internal_supplier"},
        "inter_company_invoice_reference": {"depends_on": "is_internal_supplier"},
        "unrealized_profit_loss_account": {"depends_on": "is_internal_supplier"},
        "bill_date": {"depends_on": "bill_no"},
    },
    "accounts/purchase_invoice_item.yaml": {
        "deferred_expense_account": {"depends_on": DEFERRED_OUT, "mandatory_depends_on": DEFERRED_OUT},
        "service_start_date": {"depends_on": DEFERRED_OUT, "mandatory_depends_on": DEFERRED_OUT},
        "service_end_date": {"depends_on": DEFERRED_OUT, "mandatory_depends_on": DEFERRED_OUT},
        "service_stop_date": {"depends_on": DEFERRED_OUT},
        "asset_location": {"depends_on": "is_fixed_asset"},
        "asset_category": {"depends_on": "is_fixed_asset"},
    },
    # ── Le paiement : un virement interne n'a pas de tiers ──
    "accounts/payment_entry.yaml": {
        "party_type": {"depends_on": NOT_INTERNAL, "mandatory_depends_on": NOT_INTERNAL},
        "party": {"depends_on": NOT_INTERNAL, "mandatory_depends_on": NOT_INTERNAL},
        "party_name": {"depends_on": NOT_INTERNAL},
        "party_bank_account": {"depends_on": "party"},
        "contact_person": {"depends_on": "party"},
        "contact_email": {"depends_on": "party"},
    },
    # ── L'écriture de journal : ce qui dépend de son genre ──
    "accounts/journal_entry.yaml": {
        "cheque_no": {"depends_on": "voucher_type in ['Bank Entry', 'Credit Card Entry']", "mandatory_depends_on": "voucher_type == 'Bank Entry'"},
        "cheque_date": {"depends_on": "voucher_type in ['Bank Entry', 'Credit Card Entry']", "mandatory_depends_on": "voucher_type == 'Bank Entry'"},
        "write_off_based_on": {"depends_on": "voucher_type == 'Write Off Entry'"},
        "write_off_amount": {"depends_on": "voucher_type == 'Write Off Entry'"},
        "inter_company_journal_entry_reference": {"depends_on": "voucher_type == 'Inter Company Journal Entry'"},
        "stock_asset_account": {"depends_on": "voucher_type == 'Periodic Accounting Entry'"},
        "periodic_entry_difference_account": {"depends_on": "voucher_type == 'Periodic Accounting Entry'"},
        "for_all_stock_asset_accounts": {"depends_on": "voucher_type == 'Periodic Accounting Entry'"},
    },
    # ── Les tiers ──
    "selling/customer.yaml": {
        "gender": {"depends_on": "customer_type == 'Individual'"},
        "represents_company": {"depends_on": "is_internal_customer", "mandatory_depends_on": "is_internal_customer"},
        "release_date": {"depends_on": "on_hold"},
    },
    "buying/supplier.yaml": {
        "represents_company": {"depends_on": "is_internal_supplier", "mandatory_depends_on": "is_internal_supplier"},
        "hold_type": {"depends_on": "on_hold", "mandatory_depends_on": "on_hold"},
        "release_date": {"depends_on": "on_hold"},
    },
    # ── L'article : ce qui n'a de sens que pour un article stocké, par lots, en série, immobilisé ──
    "stock/item.yaml": {
        "has_batch_no": {"depends_on": "is_stock_item"},
        "has_serial_no": {"depends_on": "is_stock_item"},
        "valuation_method": {"depends_on": "is_stock_item"},
        "shelf_life_in_days": {"depends_on": "is_stock_item"},
        "allow_negative_stock": {"depends_on": "is_stock_item"},
        "create_new_batch": {"depends_on": "has_batch_no"},
        "batch_number_series": {"depends_on": "has_batch_no"},
        "has_expiry_date": {"depends_on": "has_batch_no"},
        "retain_sample": {"depends_on": "has_batch_no"},
        "sample_quantity": {"depends_on": "retain_sample"},
        "serial_no_series": {"depends_on": "has_serial_no"},
        "use_serial_no_wise_valuation": {"depends_on": "has_serial_no"},
        "asset_category": {"depends_on": "is_fixed_asset", "mandatory_depends_on": "is_fixed_asset"},
        "auto_create_assets": {"depends_on": "is_fixed_asset"},
        "is_grouped_asset": {"depends_on": "is_fixed_asset"},
        "variant_based_on": {"depends_on": "has_variants"},
        "no_of_months": {"depends_on": DEFERRED_IN},
        "no_of_months_exp": {"depends_on": DEFERRED_OUT},
        "purchase_uom": {"depends_on": "is_purchase_item"},
        "min_order_qty": {"depends_on": "is_purchase_item"},
        "lead_time_days": {"depends_on": "is_purchase_item"},
        "sales_uom": {"depends_on": "is_sales_item"},
        "max_discount": {"depends_on": "is_sales_item"},
    },
    # ── Le mouvement de stock : la nomenclature, l'ordre de fabrication ──
    "stock/stock_entry.yaml": {
        "bom_no": {"depends_on": "from_bom", "mandatory_depends_on": "from_bom"},
        "use_multi_level_bom": {"depends_on": "from_bom"},
        "fg_completed_qty": {"depends_on": "from_bom"},
        "work_order": {"depends_on": "purpose in ['Material Transfer for Manufacture', 'Manufacture', 'Material Consumption for Manufacture']"},
    },
    # ── Le salarié : la sortie, le virement ──
    "setup/employee.yaml": {
        "relieving_date": {"depends_on": LEFT, "mandatory_depends_on": LEFT},
        "reason_for_leaving": {"depends_on": LEFT},
        "resignation_letter_date": {"depends_on": LEFT},
        "leave_encashed": {"depends_on": LEFT},
        "encashment_date": {"depends_on": LEFT},
        "held_on": {"depends_on": LEFT},
        "new_workplace": {"depends_on": LEFT},
        "feedback": {"depends_on": LEFT},
        "bank_name": {"depends_on": "salary_mode == 'Bank'"},
        "bank_ac_no": {"depends_on": "salary_mode == 'Bank'"},
        "iban": {"depends_on": "salary_mode == 'Bank'"},
    },
    # ── Le congé : la demi-journée ──
    "hr/leave_application.yaml": {
        "half_day_date": {"depends_on": "half_day", "mandatory_depends_on": "half_day"},
    },
    # ── La demande de matériel : le client d'un article qu'il fournit lui-même ──
    "stock/material_request.yaml": {
        "customer": {"depends_on": "material_request_type == 'Customer Provided'", "mandatory_depends_on": "material_request_type == 'Customer Provided'"},
        "set_from_warehouse": {"depends_on": "material_request_type == 'Material Transfer'"},
    },
    # ── La commande fournisseur : la sous-traitance ──
    "buying/purchase_order.yaml": {
        "supplier_warehouse": {"depends_on": "is_subcontracted", "mandatory_depends_on": "is_subcontracted"},
        "order_confirmation_date": {"depends_on": "order_confirmation_no"},
    },
}


def field_block(lines: list[str], field: str) -> tuple[int, int, str] | None:
    """`(début, fin, indentation des clés)` du bloc YAML du champ `field` (écriture en bloc seulement)."""
    for i, line in enumerate(lines):
        if line.strip() == f"- fieldname: {field}":
            indent = " " * (len(line) - len(line.lstrip()) + 2)
            j = i + 1
            while j < len(lines):
                nxt = lines[j].rstrip("\n")
                if not nxt.strip() or (len(nxt) - len(nxt.lstrip())) < len(indent):
                    break
                j += 1
            return i, j, indent
    return None


def _quote(expr: str) -> str:
    return '"' + expr.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply(path: Path, relative: str, write: bool) -> list[str]:
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text) or {}
    by_name = {f["fieldname"]: f for f in parsed.get("fields") or []}
    lines = text.splitlines(keepends=True)
    notes: list[str] = []

    def edit(field: str, set_keys: dict[str, str], drop: tuple[str, ...] = (), retype: str | None = None) -> None:
        found = by_name.get(field)
        if found is None:
            notes.append(f"ABSENT {field}")
            return
        done = all(found.get(k) == v for k, v in set_keys.items()) and not any(found.get(k) for k in drop)
        if done and (retype is None or found.get("fieldtype") == retype):
            return  # déjà appliqué : idempotent
        where = field_block(lines, field)
        if where is None:
            flow = next((i for i, ln in enumerate(lines) if ln.strip().startswith("- {") and f"fieldname: {field}," in ln), None)
            if flow is None or retype is not None or "required: true" in lines[flow] and "required" not in drop:
                notes.append(f"ILLISIBLE {field} (écriture inattendue)")
                return
            # Écriture en flot (`- { fieldname: x, … }`) : la clé s'insère avant l'accolade fermante.
            ln = lines[flow].replace(", required: true", "") if "required" in drop else lines[flow]
            extra = "".join(f", {k}: {v if k == 'fetch_from' else _quote(v)}" for k, v in set_keys.items())
            head, _, tail = ln.rpartition("}")
            lines[flow] = head.rstrip() + extra + " }" + tail
            notes.append(f"{field}: " + ", ".join(f"{k} = {v}" for k, v in set_keys.items()) + " (en flot)")
            return
        start, end, indent = where
        block = []
        for ln in lines[start:end]:
            key = ln.strip().removeprefix("- ").split(":", 1)[0]
            if key in drop or key in set_keys:
                continue
            if retype is not None and key == "fieldtype":
                ln = f"{indent}fieldtype: {retype}\n"
            block.append(ln)
        # Les clés neuves se posent juste APRÈS `- fieldname:`, jamais en fin de bloc : un texte sur plusieurs lignes
        # (une description avec une ligne vide) fait finir le bloc trop tôt, et la clé tombait dans le texte.
        added = [f"{indent}{k}: {v if k == 'fetch_from' else _quote(v)}\n" for k, v in set_keys.items()]
        block[1:1] = added
        lines[start:end] = block
        dropped = " (required retiré)" if "required" in drop and found.get("required") else ""
        retyped = f" (type {found.get('fieldtype')} -> {retype})" if retype and found.get("fieldtype") != retype else ""
        notes.append(f"{field}: " + ", ".join(f"{k} = {v}" for k, v in set_keys.items()) + retyped + dropped)

    for field, source in FETCHES.get(relative, {}).items():
        edit(field, {"fetch_from": source}, drop=("required",), retype=RETYPE.get(relative, {}).get(field))
    for field, conds in CONDITIONS.get(relative, {}).items():
        edit(field, conds)
    if write and notes:
        path.write_text("".join(lines), encoding="utf-8")
    return notes


def main() -> int:
    write = "--write" in sys.argv
    total = 0
    for relative in sorted(set(FETCHES) | set(CONDITIONS)):
        path = ROOT / relative
        if not path.is_file():
            print(f"  {relative}: FICHIER ABSENT")
            continue
        notes = apply(path, relative, write)
        if notes:
            total += sum(1 for n in notes if not n.startswith(("ABSENT", "ILLISIBLE")))
            print(f"  {relative}:")
            for n in notes:
                print(f"      {n}")
    print(f"\n{total} champ(s) {'modifié(s)' if write else 'à modifier'}")
    if not write and total:
        print("Rien n'a été écrit : relancez avec --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
