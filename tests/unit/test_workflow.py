"""Le Workflow : la DÉFINITION et ses règles (doc/programme.md §3.2).

Décisions éprouvées ici :
 - **un seul automate** : un état déclare le `docstatus` qu'il impose, au lieu de vivre à côté de lui ; un état qui soumet ou
   annule exige donc un DocType `submittable`, et c'est le workflow qui soumet ;
 - **un grant par transition**, jamais un rôle (texte libre par organisation chez auth) : sans `grant`, la convention
   `<module>:<doctype>:workflow_<action>` s'applique, et le grant est DÉCLARÉ à auth comme les autres ;
 - **séparation des pouvoirs** : `not_owner` dit qu'on n'approuve pas sa propre demande.

L'exécution (colonne d'état, transition conditionnelle, routes) vient dans la tranche suivante.
"""

from __future__ import annotations

import pytest

from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.schema import parse_definition

STATES = [
    {"name": "Brouillon"},
    {"name": "À approuver"},
    {"name": "Approuvé", "docstatus": "submitted"},
    {"name": "Refusé", "docstatus": "cancelled"},
]
TRANSITIONS = [
    {"action": "Soumettre", "from": "Brouillon", "to": "À approuver"},
    {"action": "Approuver", "from": "À approuver", "to": "Approuvé", "not_owner": True},
    {"action": "Refuser", "from": "À approuver", "to": "Refusé", "not_owner": True},
]


def doctype(**patch):
    base = {
        "schema_version": 1,
        "name": "Leave Request",
        "label": "Demande de congé",
        "module": "hr",
        "submittable": True,
        "workflow": {"states": STATES, "transitions": TRANSITIONS},
        "fields": [{"fieldname": "objet", "fieldtype": "Text", "required": True}],
    }
    return parse_definition({**base, **patch})


def workflow(**patch):
    """Un automate à DEUX états pour les cas où l'on ne remplace que les transitions : tout état doit rester joignable."""
    pair = [{"name": "Brouillon"}, {"name": "Approuvé", "docstatus": "submitted"}]
    return doctype(workflow={"states": pair, "transitions": TRANSITIONS, **patch})


class TestTheAutomaton:
    def test_the_first_state_is_where_a_new_document_starts(self):
        assert doctype().workflow.initial == "Brouillon"

    def test_a_state_says_which_docstatus_it_imposes(self):
        wf = doctype().workflow
        assert (wf.docstatus_of("Brouillon"), wf.docstatus_of("À approuver")) == (0, 0)
        assert (wf.docstatus_of("Approuvé"), wf.docstatus_of("Refusé")) == (1, 2)

    def test_a_transition_is_found_by_its_action(self):
        t = doctype().workflow.transition("Approuver")
        assert (t.from_, t.to, t.not_owner) == ("À approuver", "Approuvé", True)
        assert doctype().workflow.transition("Inconnue") is None


class TestWhoMayTransition:
    def test_each_transition_opens_its_own_grant_by_convention(self):
        """Un grant par transition dit exactement qui fait quoi — et il est déclaré à auth, sinon personne ne pourrait jamais transiter."""
        specs = doctype().grant_specs()
        assert specs["hr:leave_request:workflow_approuver"].description == "Approuver Demande de congé"
        assert {"hr:leave_request:workflow_soumettre", "hr:leave_request:workflow_refuser"} <= set(specs)

    def test_an_explicit_grant_replaces_the_convention(self):
        d = workflow(transitions=[{"action": "Approuver", "from": "Brouillon", "to": "Approuvé", "grant": "hr:conges:valider"}])
        assert "hr:conges:valider" in d.grant_specs() and "hr:leave_request:workflow_approuver" not in d.grant_specs()

    def test_a_description_travels_to_auth(self):
        d = workflow(transitions=[{"action": "Approuver", "from": "Brouillon", "to": "Approuvé", "description": "Valider une demande d'un subordonné"}])
        assert d.grant_specs()["hr:leave_request:workflow_approuver"].description == "Valider une demande d'un subordonné"

    @pytest.mark.parametrize("grant", ["role:manager", "@authenticated"])
    def test_a_role_never_opens_a_transition(self, grant):
        with pytest.raises(InvalidDefinition, match="transitions"):
            workflow(transitions=[{"action": "Approuver", "from": "Brouillon", "to": "Approuvé", "grant": grant}])


class TestMistakesFailAtLoad:
    def test_two_states_with_the_same_name(self):
        with pytest.raises(InvalidDefinition, match="même nom"):
            workflow(states=[{"name": "Brouillon"}, {"name": "Brouillon"}],
                     transitions=[{"action": "x", "from": "Brouillon", "to": "Brouillon"}])

    def test_two_transitions_with_the_same_action(self):
        with pytest.raises(InvalidDefinition, match="s'appellent"):
            workflow(transitions=[
                {"action": "Approuver", "from": "Brouillon", "to": "Approuvé"},
                {"action": "Approuver", "from": "Brouillon", "to": "Refusé"},
            ])

    @pytest.mark.parametrize("edge", ["from", "to"])
    def test_a_transition_towards_an_unknown_state(self, edge):
        bad = {"action": "Approuver", "from": "Brouillon", "to": "Approuvé", edge: "Nulle part"}
        with pytest.raises(InvalidDefinition, match="état inconnu"):
            workflow(transitions=[bad])

    def test_a_transition_that_goes_nowhere(self):
        with pytest.raises(InvalidDefinition, match="le même état"):
            workflow(transitions=[{"action": "Tourner", "from": "Brouillon", "to": "Brouillon"}])

    def test_a_state_no_path_reaches(self):
        """Un état sans chemin est un piège : le document ne pourra jamais y entrer, et personne ne s'en apercevra."""
        with pytest.raises(InvalidDefinition, match="aucun chemin"):
            doctype(workflow={"states": STATES, "transitions": [{"action": "Approuver", "from": "Brouillon", "to": "Approuvé"}]})

    def test_fewer_than_two_states_is_not_an_automaton(self):
        with pytest.raises(InvalidDefinition):
            workflow(states=[{"name": "Brouillon"}], transitions=[{"action": "x", "from": "Brouillon", "to": "Brouillon"}])


class TestWhatAWorkflowRequiresOfItsDocType:
    def test_submitting_states_require_a_submittable_doctype(self):
        """C'est le workflow qui soumet : sans `submittable`, `docstatus` n'existe pas pour ce type."""
        with pytest.raises(InvalidDefinition, match="submittable"):
            doctype(submittable=False)

    def test_a_draft_only_workflow_does_not_require_it(self):
        d = doctype(submittable=False, workflow={
            "states": [{"name": "Nouveau"}, {"name": "En cours"}],
            "transitions": [{"action": "Démarrer", "from": "Nouveau", "to": "En cours"}],
        })
        assert d.workflow.initial == "Nouveau" and not d.submittable

    def test_history_is_mandatory(self):
        """Qui a fait passer le document d'un état à l'autre est le sujet même : sans historique, la question reste sans réponse."""
        with pytest.raises(InvalidDefinition, match="track_changes"):
            doctype(track_changes=False)

    @pytest.mark.parametrize("patch", [
        {"istable": True, "module": None, "submittable": False, "permissions": {}},
        {"single": True, "submittable": False},
        {"source": "iam", "submittable": False},
    ])
    def test_no_workflow_on_a_child_a_single_or_a_virtual_doctype(self, patch):
        with pytest.raises(InvalidDefinition, match="workflow"):
            doctype(**patch)


# ── L'exécution : états, droits, concurrence ────────────────────────────────────────────────────

from tests.conftest import make_ctx  # noqa: E402
from xcore_doctype.document import DocumentService  # noqa: E402
from xcore_doctype.errors import DoctypeError, InvalidDocStatus, NotFound, PermissionDenied, ValidationFailed, VersionConflict  # noqa: E402
from xcore_doctype.hooks import HookRegistry  # noqa: E402
from xcore_doctype.registry import DocTypeRegistry  # noqa: E402

BASE = ["hr:leave_request:read", "hr:leave_request:write", "hr:leave_request:delete"]
SOUMETTRE = "hr:leave_request:workflow_soumettre"
APPROUVER = "hr:leave_request:workflow_approuver"
REFUSER = "hr:leave_request:workflow_refuser"  # chaque transition a SON grant : approuver n'est pas refuser


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(doctype())
    return DocumentService(reg, db, tables, Log())


def who(*grants, user="alice"):
    return make_ctx("acme", user, permissions=[*BASE, *grants])


async def demande(svc, ctx=None):
    return await svc.create(ctx or who(), "Leave Request", {"objet": "Congés d'août"})


class TestExecution:
    async def test_a_document_is_born_in_the_first_state(self, svc):
        doc = await demande(svc)
        assert doc["workflow_state"] == "Brouillon" and doc["docstatus"] == 0

    async def test_a_transition_moves_the_state_and_bumps_the_version(self, svc):
        doc = await demande(svc)
        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        assert moved["workflow_state"] == "À approuver" and moved["version"] == doc["version"] + 1

    async def test_the_grant_of_the_transition_is_required(self, svc):
        doc = await demande(svc)
        with pytest.raises(PermissionDenied):  # il peut lire et écrire, mais pas SOUMETTRE
            await svc.workflow_transition(who(), "Leave Request", doc["name"], "Soumettre", doc["version"])

    async def test_a_transition_must_start_from_the_state_the_document_is_in(self, svc):
        doc = await demande(svc)
        with pytest.raises(InvalidDocStatus, match="Brouillon"):
            await svc.workflow_transition(who(APPROUVER), "Leave Request", doc["name"], "Approuver", doc["version"])

    async def test_an_unknown_action_is_refused(self, svc):
        doc = await demande(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Téléporter", doc["version"])
        assert e.value.code == "unknown_transition"

    async def test_a_stale_version_is_refused(self, svc):
        doc = await demande(svc)
        with pytest.raises(VersionConflict):
            await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"] + 7)

    async def test_replaying_the_same_transition_passes_only_once(self, svc):
        """C'est l'UPDATE conditionnel sur (version, état) : deux approbations simultanées n'en passent qu'une."""
        doc = await demande(svc)
        await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        with pytest.raises((VersionConflict, InvalidDocStatus)):
            await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])


class TestSeparationOfPowers:
    async def test_the_author_cannot_approve_their_own_document(self, svc):
        doc = await demande(svc, who(user="alice"))
        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        with pytest.raises(DoctypeError) as e:
            await svc.workflow_transition(who(APPROUVER, user="alice"), "Leave Request", doc["name"], "Approuver", moved["version"])
        assert e.value.code == "not_owner" and e.value.status == 403

    async def test_someone_else_can(self, svc):
        doc = await demande(svc, who(user="alice"))
        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        done = await svc.workflow_transition(who(APPROUVER, user="bob"), "Leave Request", doc["name"], "Approuver", moved["version"])
        assert done["workflow_state"] == "Approuvé"


class TestOneAutomaton:
    async def test_reaching_a_submitting_state_submits_the_document_and_freezes_it(self, svc):
        doc = await demande(svc)
        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        done = await svc.workflow_transition(who(APPROUVER, user="bob"), "Leave Request", doc["name"], "Approuver", moved["version"])
        assert (done["workflow_state"], done["docstatus"]) == ("Approuvé", 1)
        with pytest.raises(DoctypeError):  # figé : un document soumis ne se modifie plus
            await svc.update(who(), "Leave Request", doc["name"], {"objet": "Autre"}, done["version"])

    async def test_reaching_a_cancelling_state_cancels_it(self, svc):
        doc = await demande(svc)
        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        refused = await svc.workflow_transition(who(REFUSER, user="bob"), "Leave Request", doc["name"], "Refuser", moved["version"])
        assert (refused["workflow_state"], refused["docstatus"]) == ("Refusé", 2)

    async def test_submit_is_refused_on_a_workflow_doctype(self, svc):
        """Laisser `/submit` passer contournerait l'étape d'approbation."""
        doc = await demande(svc)
        with pytest.raises(DoctypeError) as e:
            await svc.submit(who(), "Leave Request", doc["name"], doc["version"])
        assert e.value.code == "workflow_transition_required" and "Approuver" in e.value.message

    async def test_the_history_keeps_who_moved_the_document(self, svc):
        doc = await demande(svc)
        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        await svc.workflow_transition(who(APPROUVER, user="bob"), "Leave Request", doc["name"], "Approuver", moved["version"])
        entries = (await svc.history(who(), "Leave Request", doc["name"]))["items"]
        assert [(e["action"], e["user"]) for e in entries][:2] == [("submit", "bob"), ("update", "alice")]


class TestWhatTheDeskNeeds:
    async def test_the_server_says_which_transitions_are_open_here_and_now(self, svc):
        """Le Desk ne peut pas en décider seul : le jeton ne porte pas les grants, et `not_owner` dépend du document."""
        doc = await demande(svc, who(user="alice"))
        mine = await svc.available_transitions(who(SOUMETTRE, user="alice"), "Leave Request", doc["name"])
        assert mine["state"] == "Brouillon" and [t["action"] for t in mine["transitions"]] == ["Soumettre"]

        moved = await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        # L'auteur détient le grant mais reste l'auteur : `not_owner` lui ferme l'approbation.
        author = await svc.available_transitions(who(APPROUVER, REFUSER, user="alice"), "Leave Request", doc["name"])
        assert [t["action"] for t in author["transitions"]] == []
        boss = await svc.available_transitions(who(APPROUVER, REFUSER, user="bob"), "Leave Request", doc["name"])
        assert {t["action"] for t in boss["transitions"]} == {"Approuver", "Refuser"}
        assert next(t for t in boss["transitions"] if t["action"] == "Approuver")["docstatus"] == 1
        assert boss["version"] == moved["version"]

    async def test_an_unreadable_document_is_not_found(self, svc):
        doc = await demande(svc)
        blind = make_ctx("acme", "carl", permissions=[])
        with pytest.raises((PermissionDenied, NotFound)):
            await svc.available_transitions(blind, "Leave Request", doc["name"])


class TestListsSeeTheState:
    """L'état est une colonne : on filtre et on trie dessus comme sur `docstatus`, dans le périmètre de lecture habituel."""

    async def test_filter_a_list_by_state(self, svc):
        a = await demande(svc)
        b = await demande(svc)
        await svc.workflow_transition(who(SOUMETTRE), "Leave Request", b["name"], "Soumettre", b["version"])

        drafts = await svc.list(who(), "Leave Request", filters={"workflow_state": "Brouillon"})
        assert [d["name"] for d in drafts["items"]] == [a["name"]]
        waiting = await svc.list(who(), "Leave Request", filters={"workflow_state": "À approuver"})
        assert [d["name"] for d in waiting["items"]] == [b["name"]]
        assert (await svc.list(who(), "Leave Request", filters={"workflow_state": ["!=", "Brouillon"]}))["total"] == 1
        assert (await svc.list(who(), "Leave Request", filters={"workflow_state": ["in", ["Brouillon", "À approuver"]]}))["total"] == 2

    async def test_sort_a_list_by_state(self, svc):
        a = await demande(svc)
        b = await demande(svc)
        await svc.workflow_transition(who(SOUMETTRE), "Leave Request", b["name"], "Soumettre", b["version"])
        listed = await svc.list(who(), "Leave Request", order_by="workflow_state", descending=False)
        assert [d["workflow_state"] for d in listed["items"]] == ["Brouillon", "À approuver"]

    async def test_counting_by_state(self, svc):
        """Un bloc d'espace (« À approuver : 3 ») compte comme n'importe quel agrégat."""
        doc = await demande(svc)
        await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])
        got = await svc.aggregate(who(), "Leave Request", filters={"workflow_state": "À approuver"})
        assert got["rows"][0]["values"][0] == 1

    async def test_a_type_without_workflow_says_so(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Note", "module": "hr",
                                       "fields": [{"fieldname": "texte", "fieldtype": "Text"}]}))
        svc = DocumentService(reg, db, tables, Log())
        with pytest.raises(ValidationFailed) as e:
            await svc.list(make_ctx("acme", "alice", permissions=["hr:note:read"]), "Note", filters={"workflow_state": "x"})
        assert e.value.code == "no_workflow"

