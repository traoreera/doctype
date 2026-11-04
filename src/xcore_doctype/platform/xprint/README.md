# xprint — les gabarits d'impression des pièces commerciales

Un plugin **YAML seul** (plus dix lignes de Python pour le chargement), livré avec xcore-doctype et posé par `xcore-doctype install`
à côté du moteur, comme `xlicense` et `xpulse`.

| Dossier | Pièce | Gabarit |
|---|---|---|
| `print/Quotation/` | Devis | `standard` |
| `print/Sales Order/` | Confirmation de commande | `standard` |
| `print/Sales Invoice/` | Facture | `standard` |
| `print/Delivery Note/` | Bon de livraison (sans prix, avec signatures) | `standard` |
| `print/Purchase Order/` | Bon de commande fournisseur | `standard` |
| `print/Payment Entry/` | Reçu de paiement (A5 paysage) | `standard` |

## Comment ça marche

- **Un dossier par DocType, nommé comme lui.** Au chargement, `src/main.py` ne prend que les dossiers dont le type est chargé
  (`doctype_registry.has`) : sans le module ventes, pas de facture, et ce n'est pas une erreur (le journal `xprint loaded` liste ce
  qui est sauté). Un gabarit **fautif**, lui, fait échouer le chargement : c'est un défaut, pas un cas à taire.
- **Des documents libres (`content`)**, pas des piles de blocs : les libellés sont écrits ici, en français (« Désignation »,
  « Total TTC »), alors qu'un bloc `fields`/`totals` imprimerait ceux du DocType. Les lignes et les taxes sont des blocs `table`
  incorporés (`<div data-block='…'>`), avec un style de la galerie (`accent`, `ruled`, `bordered`…).
- **Rien n'est calculé ici** : les totaux viennent du serveur (`compute:` des DocTypes), la devise s'ajoute d'elle-même
  (`currency_from`), les liens s'impriment par leur titre.
- **Le papier à en-tête** n'est pas dans ces gabarits : c'est le fond PDF de l'organisation (Paramètres › Général), posé dessous.

## Personnaliser

- **Une organisation** refait un gabarit à l'écran (« ⋯ » de la fiche › Personnaliser l'impression) : le sien passe avant celui-ci,
  « Revenir à l'original » le rend.
- **Ajouter un gabarit** (un « duplicata », un « proforma »…) : un fichier de plus dans le dossier du type, avec un autre `name`.
  Le Desk propose alors le choix dans « Imprimer ».
- **Ajouter une pièce** : un dossier nommé exactement comme le DocType. Vérifiez avec `poetry run pytest tests/unit/test_xprint.py`
  (il charge chaque gabarit contre le jeu `doctypes/` et le rend).

## Ordre de chargement

`requires: [xcore-doctype]` suffit quand le jeu `doctypes/` est chargé par le moteur (`doctype.doctypes_dirs`). Avec un plugin par
module (dépôt `traoreera/erp`), ajoutez les modules dont on imprime les pièces dans `requires` de votre copie du manifeste, pour
qu'ils soient chargés AVANT xprint.
