# `biblio` : un plugin d'exemple en YAML, Python et JavaScript

Des livres et leurs emprunts. Chaque langage a son rôle :

```
biblio/
├── plugin.yaml            manifeste xcore : nom, dépendances, actions IPC ouvertes
├── doctypes/              YAML : les DONNÉES (champs, mise en page, conditions, série, vues, hooks cités par leur nom)
│   ├── livre.yaml
│   └── emprunt.yaml
├── workspaces/biblio.yaml YAML : la page d'accueil du module (chiffres, graphiques, raccourcis)
├── src/main.py            Python : charge le tout, déclare les grants, fournit les hooks, expose une action IPC
└── desk/index.js          JavaScript : ce que le plugin ajoute au Desk (panneau, actions de fiche, page, lien, commande)
```

| Quoi | Où | Ce que ça montre |
|---|---|---|
| Série `EMP-2026-0001` | `emprunt.yaml` `naming` | numérotation par organisation, sans trou ni doublon |
| `rendu_le` visible et obligatoire si statut = Rendu | `emprunt.yaml` `depends_on` | condition évaluée dans le Desk ET revérifiée par le serveur |
| Retour avant la sortie refusé | `main.py` `verifie_dates` (hook `validate`) | 422 sur le bon champ |
| Durée calculée | `main.py` `duree` (hook `before_save`) | le serveur écrase ce que le client envoie |
| Kanban par statut, calendrier des sorties | `emprunt.yaml` `views` | vues du Desk sans une ligne de JS |
| Tableau de bord | `workspaces/biblio.yaml` | cartes et graphiques filtrés par les droits de chacun |
| « Prêter ce livre », « Marquer rendu », panneau, page `#/x/biblio/retards` | `desk/index.js` | l'extension passe par l'API, avec les droits de l'utilisateur |
| `biblio.en_retard` | `main.py` (action IPC) | un appel d'opérateur, lecture en contexte système nommé |

Les grants viennent du YAML, par convention : `biblio:livre:read|write|delete`, `biblio:emprunt:read|write|delete`.

## L'essayer

```bash
poetry run xcore-doctype check examples/biblio/doctypes --owner biblio   # valide DocTypes, espace, extension
ln -s ../examples/biblio app/biblio                                     # xcore découvre les plugins par dossier
```

**L'extension JavaScript n'est chargée que si l'opérateur l'autorise**, et par défaut il n'en autorise aucune (`extensions: []`) : c'est
du code de confiance qui tourne dans la page de chaque utilisateur. Dans le manifeste de xcore-doctype que l'hôte charge
(`app/xcore-doctype/plugin.yaml` ; en développement c'est un lien vers `src/xcore_doctype/plugin_dir/plugin.yaml`) :

```yaml
doctype:
  desk:
    extensions: [biblio]     # ou ["*"] pour toutes
```

Puis relancer (`make dev`) et donner les grants `biblio:*` à un rôle (Paramètres > Système > Rôles). Sans eux, l'espace « Bibliothèque »
et son lien « Retards » n'apparaissent pas.

**Rien n'apparaît ?** Dans l'ordre :

1. `curl -s http://127.0.0.1:8000/app/xcore-doctype/desk/config.json` : `extensions` doit contenir `biblio`. Sinon, le journal du serveur
   dit pourquoi : `desk extension not allowed by the operator (doctype.desk.extensions)` (liste ci-dessus), ou le plugin n'est pas chargé
   (lien `app/biblio` absent, `plugin.yaml` invalide : xcore ne l'écarte qu'avec un WARNING `invalid manifest`).
2. Recharger la page du Desk : les extensions sont lues au démarrage de la page, pas à chaud.
3. Une extension qui échoue (erreur dans `setup`, mauvais `name`) affiche UNE notification et n'enregistre rien ; la console du navigateur
   donne l'erreur.

`tests/integration/test_example_biblio.py` démarre ce plugin sur un hôte réel à chaque `make check` : l'exemple ne peut pas dériver du moteur.
