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

Puis, dans la copie opérateur du manifeste de xcore-doctype (`app/xcore-doctype/plugin.yaml`), autoriser l'extension du Desk :
`doctype: { desk: { extensions: [biblio] } }`, et relancer `make dev`. Donnez les grants `biblio:*` à un rôle dans Paramètres > Système.

`tests/integration/test_example_biblio.py` démarre ce plugin sur un hôte réel à chaque `make check` : l'exemple ne peut pas dériver du moteur.
