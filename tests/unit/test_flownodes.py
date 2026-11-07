"""La section `xflow:` d'un plugin : ce qui est accepté, ce qui fait échouer son chargement, ce que l'éditeur reçoit."""

import pytest

from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.flownodes import FlowCatalog, parse_section

MAILER = {
    "nodes": [
        {
            "action": "mail.send",
            "label": "Envoyer un e-mail",
            "icon": "envelope",
            "fields": [
                {"name": "to", "label": "Destinataire", "type": "email", "required": True},
                {"name": "priority", "label": "Priorité", "type": "select", "options": ["basse", "haute"]},
            ],
            "outputs": [{"path": "message_id", "label": "Identifiant du message"}],
        }
    ],
    "events": [{"name": "mailer.received", "label": "E-mail reçu", "fields": [{"name": "sender", "label": "Expéditeur"}]}],
}


def test_a_section_is_served_with_its_plugin_name():
    catalog = FlowCatalog()
    catalog.declare("mailer", MAILER)
    served = catalog.as_dict()
    assert served["nodes"][0]["plugin"] == "mailer"
    assert served["nodes"][0]["fields"][0] == {"name": "to", "label": "Destinataire", "type": "email", "required": True, "options": []}
    assert served["events"] == [{"plugin": "mailer", "name": "mailer.received", "label": "E-mail reçu", "fields": [{"name": "sender", "label": "Expéditeur"}]}]


def test_no_section_and_unloading_leave_nothing():
    catalog = FlowCatalog()
    assert parse_section("mailer", None).nodes == []
    catalog.declare("mailer", MAILER)
    catalog.unregister("mailer")
    assert catalog.as_dict() == {"nodes": [], "events": []}
    catalog.declare("mailer", MAILER)
    catalog.declare("mailer", {})  # une section vidée au rechargement efface l'ancienne
    assert catalog.as_dict() == {"nodes": [], "events": []}


@pytest.mark.parametrize(
    ("section", "said"),
    [
        ({"events": [{"name": "crm.lead", "label": "x"}]}, "doit commencer par « mailer. »"),
        ({"events": [{"name": "doctype.customer.after_insert", "label": "x"}]}, "doit commencer par « mailer. »"),
        ({"nodes": [{"action": "a", "label": "A"}, {"action": "a", "label": "B"}]}, "déclarée deux fois"),
        ({"nodes": [{"action": "a", "label": "A", "fields": [{"name": "x", "label": "X", "type": "select"}]}]}, "sans options"),
        ({"nodes": [{"action": "a", "label": "A", "fields": [{"name": "x", "label": "X", "options": ["o"]}]}]}, "sans être une liste"),
        ({"nodes": [{"action": "a", "label": "A", "outputs": [{"path": "a..b", "label": "X"}]}]}, "outputs"),
        ({"nodes": [{"action": "a", "label": "A", "run": "rm -rf /"}]}, "run"),
        ({"nodez": []}, "nodez"),
    ],
)
def test_a_faulty_section_fails_the_plugin_load_and_says_why(section, said):
    with pytest.raises(InvalidDefinition) as caught:
        parse_section("mailer", section)
    assert said in str(caught.value)
