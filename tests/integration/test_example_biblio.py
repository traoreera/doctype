"""Le plugin d'exemple `examples/biblio` (YAML + Python + JavaScript) tourne vraiment : sur un hôte réel, ses DocTypes se chargent, ses hooks
calculent et refusent, sa série numérote, son espace s'affiche, son action IPC répond et son extension du Desk est servie quand l'opérateur
l'autorise. Sans ce test, un exemple dérive en silence du moteur qu'il illustre."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tests.integration.conftest import boot_host

BIBLIO = Path(__file__).resolve().parents[2] / "examples" / "biblio"
GRANTS = ["biblio:livre:read", "biblio:livre:write", "biblio:emprunt:read", "biblio:emprunt:write"]


async def test_the_example_plugin_runs(tmp_path):
    config = {"desk": {"extensions": ["biblio"]}}
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins={"biblio": BIBLIO}, doctype_config=config) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        livre = await h.http("POST", "/doc/Livre", token=ada, json={"data": {"titre": "Les Soleils des indépendances", "auteur": "Ahmadou Kourouma"}})
        assert livre.status_code == 201, livre.text
        name = livre.json()["name"]

        # La série numérote, `sortie` prend le jour, le hook `biblio.duree` calcule `jours` (et écrase ce que le client envoie).
        e = await h.http("POST", "/doc/Emprunt", token=ada, json={"data": {"livre": name, "emprunteur": "Awa", "sortie": "2026-10-01", "jours": 999}})
        assert e.status_code == 201, e.text
        assert e.json()["name"].startswith(f"EMP-{date.today().year}-")
        assert e.json()["data"]["jours"] == (date.today() - date(2026, 10, 1)).days

        # Le hook `biblio.verifie_dates` refuse un retour avant la sortie, sur le bon champ.
        bad = await h.http("POST", "/doc/Emprunt", token=ada, json={"data": {"livre": name, "emprunteur": "Awa", "sortie": "2026-10-10", "statut": "Rendu", "rendu_le": "2026-10-01"}})
        assert bad.status_code == 422 and bad.json()["detail"]["details"][0]["field"] == "rendu_le"
        # La condition du YAML : rendu sans date de retour, refusé par le serveur.
        assert (await h.http("POST", "/doc/Emprunt", token=ada, json={"data": {"livre": name, "emprunteur": "Awa", "statut": "Rendu"}})).status_code == 422

        # L'espace « Bibliothèque » et son tableau de bord.
        spaces = (await h.http("GET", "/workspaces", token=ada)).json()
        assert "biblio" in [w["name"] for w in spaces]

        # L'action IPC d'opérateur : un emprunt en cours dont le retour prévu est passé.
        late = await h.http("POST", "/doc/Emprunt", token=ada, json={"data": {"livre": name, "emprunteur": "Moussa", "sortie": "2026-01-02", "retour_prevu": "2026-01-20"}})
        found = await h.action("biblio.en_retard", {"tenant": "acme"}, token=ada, plugin="biblio")
        assert [i["name"] for i in found["items"]] == [late.json()["name"]]

        # L'extension JavaScript du Desk est annoncée par config.json, puis servie.
        conf = (await h.client.get(f"{h.base}/desk/config.json")).json()
        url = next(x["url"] for x in conf["extensions"] if x["name"] == "biblio")
        js = await h.client.get(url if url.startswith("http") else f"{h.base}/desk/{url}")
        assert js.status_code == 200 and "defineExtension" in js.text
