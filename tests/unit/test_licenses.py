"""La règle de licence du Desk, vérifiée à chaque exécution plutôt que retenue de mémoire.

PrimeVue 5, `@primeuix/themes` 3 et `primeicons` 8 sont sous la « PrimeUI License » (clé de licence, gratuite seulement sous certains
seuils, pas de redistribution en bibliothèque). Les versions précédentes sont MIT : `ui/package.json` les épingle. Un
`pnpm update --latest` qui franchirait la limite doit faire échouer la CI, pas passer inaperçu.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "ui"
# paquet -> version majeure MIT à ne pas dépasser
MIT_MAJOR = {"primevue": 4, "@primeuix/themes": 2, "primeicons": 7}


def package_json() -> dict:
    return json.loads((UI / "package.json").read_text())


@pytest.mark.parametrize(("name", "major"), sorted(MIT_MAJOR.items()))
def test_the_declared_range_cannot_reach_the_licensed_major(name, major):
    """`^4.5.5` accepte 4.x et jamais 5.0 : on exige exactement ce plafond (pas `*`, pas `latest`, pas `>=`)."""
    declared = package_json()["dependencies"][name]
    assert re.fullmatch(rf"[\^~]{major}\.\d+\.\d+", declared), f"{name}: {declared!r} peut franchir la licence MIT (attendu ^{major}.x.y)"


@pytest.mark.parametrize(("name", "major"), sorted(MIT_MAJOR.items()))
def test_the_installed_version_is_the_mit_one(name, major):
    manifest = UI / "node_modules" / name / "package.json"
    if not manifest.is_file():
        pytest.skip("dépendances du Desk non installées (make install-ui)")
    installed = json.loads(manifest.read_text())
    assert int(installed["version"].split(".")[0]) == major, f"{name} {installed['version']} n'est pas en version majeure {major}"
    assert installed["license"] == "MIT", f"{name}: licence {installed['license']!r}"


def test_the_lockfile_does_not_pin_a_licensed_major():
    lock = (UI / "pnpm-lock.yaml").read_text()
    for name, major in MIT_MAJOR.items():
        versions = {int(v.split(".")[0]) for v in re.findall(rf"(?m)^\s+'?{re.escape(name)}@(\d+\.\d+\.\d+)", lock)}
        assert versions, f"{name} absent du verrou"
        assert max(versions) <= major, f"{name}: le verrou contient la version majeure {max(versions)}"
