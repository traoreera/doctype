# Publier xcore-doctype sur PyPI

La publication est faite par GitHub Actions (`.github/workflows/publish.yml`). Personne n'envoie le paquet depuis son poste, et
aucun mot de passe ni jeton PyPI n'est stocké dans le dépôt : c'est le **trusted publishing**. PyPI fait confiance à ce workflow de
ce dépôt, dans un environnement nommé, au moyen d'un jeton à usage unique fourni par GitHub.

## Ce que fait le workflow

| Déclencheur | Étapes |
|---|---|
| un tag `vX.Y.Z` poussé | la CI complète → construction → contrôle → **PyPI** → release GitHub avec les mêmes fichiers |
| « Run workflow » (onglet Actions) | la CI complète → construction → contrôle → **TestPyPI** |

La construction (`make build`) génère les modules embarqués depuis `vendor/`, construit le Desk (types vérifiés), puis lance
`poetry build`. Le contrôle (`scripts/check_dist.py`, puis `twine check --strict`) refuse de publier dans ces cas :
- le tag ne correspond pas à la version du `pyproject.toml` ;
- le tag n'est pas une version stable (`v0.2.0rc1` ne déclenche pas la publication : une préversion s'essaie sur TestPyPI avec « Run workflow ») ;
- il manque le Desk, un shim `main.py`, un plugin de plateforme ou une extension (ou, dans l'archive source, les sources du paquet) ;
- un secret (`.env`, `*.pem`), une base `.db`, un cache ou `node_modules` est entré dans le paquet ;
- la page PyPI n'a pas de description.

## À faire une seule fois

1. **Sur pypi.org** (le projet n'existe pas encore : « pending publisher ») : Account settings › Publishing › *Add a new pending
   publisher* :
   - PyPI Project Name : `xcore-doctype`
   - Owner : `traoreera` · Repository name : `doctype`
   - Workflow name : `publish.yml` · Environment name : `pypi`
2. **Sur test.pypi.org** : la même chose, avec l'environnement `testpypi`. Le compte TestPyPI est distinct de celui de PyPI.
3. **Sur GitHub** (Settings › Environments) : créez `pypi` et `testpypi`. Sur `pypi`, ajoutez-vous en *Required reviewer* : chaque
   publication attendra votre clic, une dernière occasion de l'arrêter.

## Publier une version

1. Changez la version au même numéro dans `pyproject.toml` (`poetry version 0.2.0`), `src/xcore_doctype/plugin_dir/plugin.yaml`,
   `src/xcore_doctype/system_plugin_dir/plugin.yaml` et `ui/package.json` (`tests/unit/test_cli.py` refuse un manifeste en retard).
2. Vérifiez en local, exactement comme la CI : `make release-check`.
3. Commitez, puis fusionnez dans `master`.
4. Posez et poussez le tag sur ce commit :

   ```bash
   git tag v0.2.0
   ```

   ```bash
   git push origin v0.2.0
   ```

5. Suivez l'onglet Actions : CI, construction, approbation de l'environnement `pypi`, publication, release.

Pour essayer avant : lancez « Publier sur PyPI » à la main depuis l'onglet Actions (TestPyPI), puis installez la version de test :

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ xcore-doctype
```

## À savoir

- **Une version publiée ne se republie pas.** PyPI refuse un numéro déjà envoyé, même si le fichier a été retiré. En cas d'erreur,
  publiez la version suivante.
- **TestPyPI suit la même règle.** Pour essayer deux fois la même version, ajoutez un suffixe (`0.2.0rc1`, puis `0.2.0rc2`).
- **Installer le paquet ne suffit pas à charger le plugin.** xcore ne découvre que des dossiers : après `pip install xcore-doctype`,
  l'opérateur lance `xcore-doctype install ./app` (voir `docs/guide.md` §1).
- **Les modules de `vendor/` doivent rester clonables par GitHub Actions.** Ce sont des sous-modules git : s'ils deviennent privés,
  le checkout de la CI échouera faute d'accès.
