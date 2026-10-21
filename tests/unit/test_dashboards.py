"""Blocs d'espace de travail (cartes, graphiques, listes rapides, cartes de liens) et vues d'un DocType (Kanban, Calendrier, Gantt, tableau de bord).

Une déclaration fausse est une erreur de CHARGEMENT (champ inconnu, somme d'un texte, filtre invalide), jamais une carte vide en production ; ce
qu'un utilisateur reçoit est FILTRÉ par ses droits.
"""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.dashboards import ChartBlock, NumberCardBlock, default_dashboard
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.workspaces import parse_workspace


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def dt(name="Task", **over):
    base = {
        "schema_version": 1,
        "name": name,
        "module": "shop",
        "fields": [
            {"fieldname": "title", "fieldtype": "Text", "required": True, "in_list_view": True},
            {"fieldname": "state", "fieldtype": "Select", "options": ["À faire", "En cours", "Fait"], "default": "À faire", "in_list_view": True},
            {"fieldname": "prio", "fieldtype": "Select", "options": ["Basse", "Haute"]},
            {"fieldname": "start", "fieldtype": "Date"},
            {"fieldname": "stop", "fieldtype": "Date"},
            {"fieldname": "hours", "fieldtype": "Number"},
            {"fieldname": "cost", "fieldtype": "Decimal", "decimals": 2},
            {"fieldname": "note", "fieldtype": "Text"},
        ],
    }
    return {**base, **over}


def reg_with(*raws):
    reg = DocTypeRegistry()
    for raw in raws:
        reg.register(parse_definition(raw))
    return reg


def ws(*blocks, **over):
    return {"schema_version": 1, "name": "shop", "label": "Boutique", "blocks": list(blocks), **over}


def load(reg, tmp_path, workspace):
    d = tmp_path / "ws"
    d.mkdir(exist_ok=True)
    (d / "shop.yaml").write_text(yaml.safe_dump(workspace, allow_unicode=True))
    return reg.load_workspaces(d, owner="shop")


CARD = {"type": "number_card", "label": "Heures", "doctype": "Task", "function": "sum", "field": "hours"}
CHART = {"type": "chart", "label": "Par état", "doctype": "Task", "kind": "donut", "group_by": "state"}


class TestBlockModels:
    def test_every_block_type_parses_with_its_defaults(self):
        d = parse_workspace(
            ws(
                {"type": "header", "text": "Suivi"},
                {"type": "paragraph", "text": "Bienvenue"},
                {"type": "spacer"},
                CARD,
                CHART,
                {"type": "quick_list", "label": "Récents", "doctype": "Task"},
                {"type": "card", "title": "Liens", "links": [{"doctype": "Task"}]},
                {"type": "shortcut", "doctype": "Task"},
            )
        )
        card, chart = d.blocks[3], d.blocks[4]
        assert (card.cols, card.function, chart.cols, chart.kind, chart.limit) == (3, "sum", 6, "donut", 10)
        assert d.doctypes() == ["Task", "Task", "Task"] and len(d.measured()) == 2

    @pytest.mark.parametrize(
        "block, fragment",
        [
            ({**CARD, "color": "pink"}, "color"),  # une teinte de la palette, pas une couleur libre
            ({**CARD, "cols": 5}, "cols"),
            ({**CARD, "function": "median"}, "function"),
            ({**CARD, "script": "x"}, "script"),
            ({**CHART, "kind": "radar"}, "kind"),
            ({**CHART, "periods": 0}, "periods"),
            ({**CHART, "periods": "6"}, "periods"),  # pas de coercion
            ({**CHART, "limit": 51}, "limit"),
            ({**CHART, "bucket": "week"}, "bucket"),
            ({k: v for k, v in CHART.items() if k != "group_by"}, "group_by"),
            ({**CHART, "group_by": "Bad Name"}, "group_by"),
            ({"type": "quick_list", "label": "x", "doctype": "Task", "limit": 11}, "limit"),
            ({"type": "card", "title": "x", "links": []}, "links"),
            ({"type": "paragraph", "text": ""}, "text"),
            ({"type": "paragraph", "text": "x" * 501}, "text"),
            ({"type": "iframe", "src": "x"}, "blocks"),
        ],
    )
    def test_a_bad_block_is_refused_with_its_path(self, block, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            parse_workspace(ws(block))


class TestWorkspaceLoading:
    def test_good_blocks_load(self, tmp_path):
        reg = reg_with(dt())
        assert load(reg, tmp_path, ws(CARD, CHART, {"type": "quick_list", "label": "R", "doctype": "Task", "filters": {"state": "Fait"}})) == ["shop"]

    @pytest.mark.parametrize(
        "block, fragment",
        [
            ({**CARD, "doctype": "Nope"}, "DocType inconnu"),
            ({**CARD, "field": "note"}, "seul un Number, un Decimal"),
            ({**CARD, "field": "ghost"}, "champ inconnu"),
            ({**CARD, "function": "count", "field": "hours"}, "n'a pas de sens avec `function: count`"),
            ({**CARD, "field": None, "function": "avg"}, "exige `field`"),
            ({**CARD, "filters": {"ghost": 1}}, "champs inconnus"),
            ({**CARD, "filters": {"docstatus": 1}}, "n'est pas soumettable"),  # la liste ouverte au clic ne saurait montrer ce filtre
            ({**CARD, "filters": {"name": "x"}}, "filters.name"),
            ({**CARD, "filters": {"hours": "beaucoup"}}, "filtre invalide"),
            ({**CARD, "filters": {"state": "Inconnu"}}, "filtre invalide"),
            ({**CHART, "group_by": "note"}, "regroupement impossible"),
            ({**CHART, "group_by": "ghost"}, "champ inconnu"),
            ({**CHART, "function": "avg", "field": "hours"}, "count ou sum"),  # une moyenne n'est pas une part d'un total
            ({**CHART, "kind": "percentage", "function": "max", "field": "hours"}, "count ou sum"),
            ({**CHART, "bucket": "month"}, "n'a de sens que pour une date"),
            ({**CHART, "periods": 6}, "par date"),
            ({**CHART, "group_by": "start", "periods": 130, "bucket": "month"}, "entre 1 et 120"),
        ],
    )
    def test_a_block_that_cannot_compute_fails_at_load_time(self, tmp_path, block, fragment):
        block = {k: v for k, v in block.items() if v is not None}
        reg = reg_with(dt())
        with pytest.raises(InvalidDefinition, match=fragment):
            load(reg, tmp_path, ws(block))
        assert reg.workspaces() == []  # tout ou rien

    def test_a_submittable_type_may_filter_on_its_state(self, tmp_path):
        reg = reg_with(dt(submittable=True))
        block = {k: v for k, v in {**CARD, "function": "count", "filters": {"docstatus": 0}}.items() if k != "field"}
        assert load(reg, tmp_path, ws(block)) == ["shop"]

    def test_bars_and_lines_may_average_but_a_pie_must_add_up(self, tmp_path):
        reg = reg_with(dt())
        ok = {**CHART, "kind": "bar", "function": "avg", "field": "hours"}
        assert load(reg, tmp_path, ws(ok, {**ok, "kind": "line", "label": "L"}, {**CHART, "function": "sum", "field": "hours", "label": "S"})) == ["shop"]

    def test_a_quick_list_filter_is_checked_too(self, tmp_path):
        reg = reg_with(dt())
        with pytest.raises(InvalidDefinition, match="champs inconnus"):
            load(reg, tmp_path, ws({"type": "quick_list", "label": "R", "doctype": "Task", "filters": {"ghost": 1}}))

    def test_a_card_of_links_and_a_quick_list_must_point_at_loaded_types(self, tmp_path):
        reg = reg_with(dt())
        with pytest.raises(InvalidDefinition, match="DocType inconnu"):
            load(reg, tmp_path, ws({"type": "card", "title": "x", "links": [{"doctype": "Task"}, {"doctype": "Nope"}]}))
        with pytest.raises(InvalidDefinition, match="DocType inconnu"):
            load(reg, tmp_path, ws({"type": "quick_list", "label": "x", "doctype": "Nope"}))

    def test_a_block_on_a_child_table_is_refused(self, tmp_path):
        line = {"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Number"}]}
        parent = dt("Order", fields=[{"fieldname": "lines", "fieldtype": "Table", "options": "Line"}])
        reg = reg_with(line, parent)
        with pytest.raises(InvalidDefinition, match="DocType enfant"):
            load(reg, tmp_path, ws({"type": "number_card", "label": "x", "doctype": "Line"}))


# ── Vues d'un DocType ─────────────────────────────────────────────────────────────────────────


def with_views(**views):
    return dt(views=views)


class TestViewsDefinition:
    def test_a_doctype_without_views_offers_list_report_and_dashboard(self):
        d = parse_definition(dt())
        assert d.available_views() == ["list", "report", "dashboard"] and d.effective_views()["default"] == "list"

    def test_declared_views_join_in_the_selectors_order(self):
        d = parse_definition(
            with_views(kanban={"field": "state"}, gantt={"start": "start", "end": "stop"}, calendar={"start": "start"}, default="kanban")
        )
        assert d.available_views() == ["list", "report", "dashboard", "kanban", "calendar", "gantt"]
        v = d.effective_views()
        assert v["default"] == "kanban"
        assert v["kanban"] == {"field": "state", "card_fields": ["title"]}  # les colonnes de la liste, sans le champ des colonnes
        assert v["calendar"] == {"start": "start"} and v["gantt"] == {"start": "start", "end": "stop"}

    @pytest.mark.parametrize(
        "views, fragment",
        [
            ({"kanban": {"field": "ghost"}}, "champ inconnu"),
            ({"kanban": {"field": "title"}}, "un Select"),
            ({"kanban": {"field": "prio"}}, "required: true"),  # sans valeur par défaut, des documents n'auraient pas de colonne
            ({"kanban": {"field": "state", "card_fields": ["ghost"]}}, "champ inconnu"),
            ({"kanban": {"field": "state", "card_fields": list("abcde")}}, "card_fields"),
            ({"calendar": {"start": "title"}}, "une Date"),
            ({"calendar": {"start": "start", "end": "hours"}}, "une Date"),
            ({"calendar": {"start": "start", "color": "title"}}, "un Select"),
            ({"calendar": {"start": "start", "title": "ghost"}}, "champ inconnu"),
            ({"gantt": {"start": "start", "end": "start"}}, "deux champs différents"),
            ({"gantt": {"start": "start"}}, "end"),
            ({"gantt": {"start": "start", "end": "note"}}, "une Date"),
            ({"default": "kanban"}, "n'est pas déclarée"),
            ({"default": "map"}, "default"),
            ({"unknown": 1}, "unknown"),
            ({"kanban": {"field": "state", "wip": 3}}, "wip"),
            ({"tree": {"parent": "title"}}, "un Link"),
        ],
    )
    def test_a_bad_view_is_refused_at_load(self, views, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            parse_definition(with_views(**views))

    def test_a_tree_view_needs_a_link_to_its_own_doctype(self):
        fields = [
            {"fieldname": "title", "fieldtype": "Text"},
            {"fieldname": "parent", "fieldtype": "Link", "options": "Task"},
        ]
        d = parse_definition(dt(fields=fields, views={"tree": {"parent": "parent"}}))
        assert d.available_views()[-1] == "tree"
        assert d.effective_views()["tree"] == {"parent": "parent"}

    def test_a_tree_parent_that_links_elsewhere_is_refused(self):
        fields = [
            {"fieldname": "title", "fieldtype": "Text"},
            {"fieldname": "parent", "fieldtype": "Link", "options": "Other"},
        ]
        with pytest.raises(InvalidDefinition, match="il faut un Link vers 'Task'"):
            parse_definition(dt(fields=fields, views={"tree": {"parent": "parent"}}))

    def test_a_required_select_is_a_valid_kanban_column(self):
        raw = dt()
        raw["fields"][2] = {"fieldname": "prio", "fieldtype": "Select", "options": ["Basse", "Haute"], "required": True}
        assert parse_definition({**raw, "views": {"kanban": {"field": "prio"}}}).available_views()[-1] == "kanban"

    def test_a_child_table_has_no_views(self):
        raw = {"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Number"}], "views": {}}
        with pytest.raises(InvalidDefinition, match="views"):
            parse_definition(raw)


class TestDoctypeDashboard:
    def test_the_generated_dashboard_is_always_computable(self):
        # Chaque bloc généré doit passer le contrôle que subissent ceux qu'un plugin écrit : il ne peut pas tomber en panne tout seul.
        from xcore_doctype.dashboards import check_measured

        for raw in (dt(), dt(submittable=True), dt(fields=[{"fieldname": "x", "fieldtype": "Text"}])):
            defn = parse_definition(raw)
            reg = reg_with(raw)
            blocks = default_dashboard(defn)
            assert blocks[0]["label"] == "Task"
            for b in blocks:
                model = {"number_card": NumberCardBlock, "chart": ChartBlock}[b["type"]].model_validate(b)
                assert check_measured(model, reg._items.get, None) == [], b

    def test_the_generated_dashboard_follows_the_definition(self):
        labels = [b["label"] for b in default_dashboard(parse_definition(dt(submittable=True)))]
        assert labels == ["Task", "hours (somme)", "cost (somme)", "Créés par mois", "Par état", "Par state", "Par prio"]
        plain = [b["label"] for b in default_dashboard(parse_definition(dt(fields=[{"fieldname": "x", "fieldtype": "Text"}])))]
        assert plain == ["Task", "Créés par mois"]

    def test_the_generated_cards_fill_their_rows(self):
        # Les cartes de chiffres se partagent les 12 colonnes ; un graphique à côté d'un anneau remplit aussi la rangée.
        for raw in (dt(), dt(submittable=True), dt(fields=[{"fieldname": "x", "fieldtype": "Text"}]), dt(fields=[{"fieldname": "x", "fieldtype": "Number"}])):
            blocks = default_dashboard(parse_definition(raw))
            numbers = [b["cols"] for b in blocks if b["type"] == "number_card"]
            assert sum(numbers) == 12, numbers
            charts = [b["cols"] for b in blocks if b["type"] == "chart"]
            assert charts[0] + (charts[1] if len(charts) > 1 else 0) == 12, charts

    def test_the_generated_evolution_follows_the_date_the_type_announces(self):
        # La date de la pièce (Gantt, puis calendrier) plutôt que la date de saisie ; une date qui n'existe pas ou n'est pas une Date ne compte pas.
        def axis(views):
            blocks = default_dashboard(parse_definition(dt(views=views)))
            chart = next(b for b in blocks if b["type"] == "chart")
            return chart["group_by"], chart["label"]

        assert axis({"gantt": {"start": "start", "end": "stop"}, "calendar": {"start": "stop"}}) == ("start", "start par mois")
        assert axis({"calendar": {"start": "stop"}}) == ("stop", "stop par mois")
        assert axis({}) == ("creation", "Créés par mois")

    def test_meta_carries_the_views_and_blocks_name_their_doctype(self):
        v = parse_definition(dt()).effective_views()
        assert v["dashboard"][0]["doctype"] == "Task" and all(b["doctype"] == "Task" for b in v["dashboard"])

    def test_a_declared_dashboard_replaces_the_generated_one(self):
        d = parse_definition(with_views(dashboard=[{"type": "number_card", "label": "À faire", "filters": {"state": "À faire"}}]))
        assert [b["label"] for b in d.effective_views()["dashboard"]] == ["À faire"]

    def test_its_blocks_are_checked_against_the_doctype_at_registration(self):
        bad = [
            ({"type": "number_card", "label": "x", "function": "sum", "field": "note"}, "seul un Number"),
            ({"type": "number_card", "label": "x", "filters": {"ghost": 1}}, "champs inconnus"),
            ({"type": "chart", "label": "x", "group_by": "note"}, "regroupement impossible"),
            ({"type": "chart", "label": "x", "group_by": "state", "doctype": "Task"}, "n'a pas de sens ici"),  # le type est implicite
        ]
        for block, fragment in bad:
            with pytest.raises(InvalidDefinition, match=fragment):
                reg_with(with_views(dashboard=[block]))

    def test_a_good_declared_dashboard_registers(self):
        reg = reg_with(
            with_views(
                dashboard=[
                    {"type": "header", "text": "Suivi"},
                    {"type": "number_card", "label": "Heures", "function": "sum", "field": "hours"},
                    {"type": "chart", "label": "Par mois", "group_by": "start", "periods": 6},
                ]
            )
        )
        assert reg.get("Task").definition.views is not None


# ── Ce que l'utilisateur reçoit : filtré par ses droits ──────────────────────────────────────


SECRET = dt("Secret", fields=[{"fieldname": "x", "fieldtype": "Number"}])


def service(reg, db, tables):
    return DocumentService(reg, db, tables, Log())


def spaces(svc, perms):
    return svc.list_workspaces(make_ctx("acme", "alice", permissions=perms))


def kinds(space):
    return [b["type"] for b in space["blocks"]]


class TestFiltering:
    def build(self, tmp_path, *blocks):
        reg = reg_with(dt(), SECRET)
        load(reg, tmp_path, ws(*blocks))
        return reg

    def test_blocks_about_an_unreadable_type_disappear(self, db, tables, tmp_path):
        reg = self.build(
            tmp_path,
            {**CARD, "label": "Tâches"},
            {**CARD, "label": "Secret", "doctype": "Secret", "function": "sum", "field": "x"},
            CHART,
            {"type": "quick_list", "label": "Q secret", "doctype": "Secret"},
            {"type": "quick_list", "label": "Q", "doctype": "Task"},
        )
        (space,) = spaces(service(reg, db, tables), ["shop:task:read"])
        assert [b["label"] for b in space["blocks"]] == ["Tâches", "Par état", "Q"]

    def test_a_block_reaches_the_desk_as_a_description_not_as_code(self, db, tables, tmp_path):
        reg = self.build(tmp_path, {**CARD, "filters": {"state": "Fait"}, "color": "green"}, CHART)
        (space,) = spaces(service(reg, db, tables), ["shop:task:read"])
        assert space["blocks"][0] == {
            "type": "number_card", "label": "Heures", "doctype": "Task", "function": "sum", "field": "hours",
            "filters": {"state": "Fait"}, "color": "green", "cols": 3,
        }
        assert space["blocks"][1]["kind"] == "donut" and space["blocks"][1]["group_by"] == "state"

    def test_a_quick_list_tells_the_desk_how_to_title_its_rows(self, db, tables, tmp_path):
        reg = reg_with(dt(title_field="title", submittable=True))
        load(reg, tmp_path, ws({"type": "quick_list", "label": "Q", "doctype": "Task"}))
        (space,) = spaces(service(reg, db, tables), ["shop:task:read"])
        block = space["blocks"][0]
        assert (block["title_field"], block["submittable"], block["limit"]) == ("title", True, 5)

    def test_a_card_of_links_keeps_only_the_readable_ones(self, db, tables, tmp_path):
        reg = self.build(tmp_path, {"type": "card", "title": "Liens", "links": [{"doctype": "Task", "label": "Mes tâches"}, {"doctype": "Secret"}]})
        (space,) = spaces(service(reg, db, tables), ["shop:task:read"])
        assert space["blocks"] == [{"type": "card", "title": "Liens", "cols": 4, "links": [{"doctype": "Task", "label": "Mes tâches", "icon": None}]}]
        assert spaces(service(reg, db, tables), ["shop:other:read"]) == []  # aucun lien lisible : la carte, puis l'espace, disparaissent

    def test_text_alone_never_keeps_a_workspace_alive(self, db, tables, tmp_path):
        reg = self.build(tmp_path, {"type": "header", "text": "T"}, {"type": "paragraph", "text": "bonjour"}, {**CARD, "doctype": "Secret", "field": "x"})
        assert spaces(service(reg, db, tables), ["shop:task:read"]) == []  # un titre et une phrase, mais rien à montrer dessous

    def test_titles_texts_and_spacers_only_survive_next_to_something_visible(self, db, tables, tmp_path):
        reg = self.build(
            tmp_path,
            {"type": "header", "text": "Vide"},
            {"type": "paragraph", "text": "invisible"},
            {**CARD, "doctype": "Secret", "field": "x"},
            {"type": "spacer"},
            {"type": "header", "text": "Plein"},
            {"type": "paragraph", "text": "explication"},
            CARD,
            {"type": "spacer"},
            {"type": "paragraph", "text": "note"},
        )
        (space,) = spaces(service(reg, db, tables), ["shop:task:read"])
        got = [b.get("text") or b["type"] for b in space["blocks"]]
        # L'espacement qui suivait la section vide reste devant la suivante ; l'espacement final disparaît, la note de bas d'espace reste.
        assert got == ["spacer", "Plein", "explication", "number_card", "note"]

    def test_a_card_nobody_can_read_leaves_the_workspace_empty_so_it_is_dropped(self, db, tables, tmp_path):
        reg = self.build(tmp_path, {**CARD, "doctype": "Secret", "field": "x"})
        assert spaces(service(reg, db, tables), ["shop:task:read"]) == []


class TestShippedApps:
    """Les espaces et DocTypes livrés avec le dépôt se chargent : ce que le Desk montre en démonstration est contrôlé comme le reste."""

    def test_every_shipped_doctype_has_a_computable_dashboard(self):
        from xcore_doctype import system_plugin_dir
        from xcore_doctype.dashboards import check_measured
        from tests.support import FIXTURES

        reg = DocTypeRegistry()
        folders = [system_plugin_dir() / "doctypes", *sorted(p / "doctypes" for p in FIXTURES.iterdir() if (p / "doctypes").is_dir())]
        for folder in folders:
            reg.load_dir(folder)
        for name in reg.names():
            defn = reg.get(name).definition
            if defn.istable:
                continue
            for b in defn.effective_views()["dashboard"]:
                if b["type"] == "header":
                    continue
                model = {"number_card": NumberCardBlock, "chart": ChartBlock}[b["type"]].model_validate(b)
                assert check_measured(model, reg._items.get, None) == [], (name, b)
