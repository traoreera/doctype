# Propositions pour `traoreera/xcore` (11 novembre 2026)

Six propositions, toutes nées d'un **manque rencontré** en écrivant le moteur (`traoreera/doctype`) et les modules ERP
(`traoreera/erp`). Chacune dit où est le contournement aujourd'hui, ce qu'on propose, et ce que ça fait gagner. Les deux tickets
perf déjà ouverts chez xcore (#305 le `Tracer._spans` non borné et les imports celery/sqlalchemy, #306 le coût d'import par worker
sandbox) ne sont pas repris ici : ils tiennent debout tels quels, et §6 ne fait que leur apporter une mesure venue de cette pile.

Ordre : le moins de code xcore pour le plus de bugs évités en aval.

---

## 1. File de travaux transactionnelle (« outbox ») : un effet extérieur APRÈS le commit

**Constat.** Tout hook du moteur tourne *dans* la transaction. Un effet extérieur qui s'y produit ne peut plus être défait :
le module `mail` de l'ERP remet le message au serveur SMTP pendant la soumission de `Send Email`, et son `CLAUDE.md` le dit noir
sur blanc — « the SMTP send happens inside the submit transaction : if a later write fails, the mail is out but the document stays
draft, and re-submitting sends a second copy ». Le même trou attend les webhooks, les notifications et toute intégration.

`xworker` (Celery) sait exécuter une tâche au moins une fois, mais l'`enqueue` part **tout de suite** : enfilée dans un hook, la
tâche s'exécute même si la transaction est annulée ensuite ; enfilée après le commit, elle est perdue si le processus meurt entre
les deux. C'est le problème de l'outbox, et il ne se résout pas sans un passage par la base.

**Proposition.** Un service `outbox` :

- `await outbox.defer(plugin, task, payload, *, key=None)` dépose une INTENTION ; l'écriture se fait sur la connexion que
  l'appelant fournit (c'est le moteur qui tient la transaction, xcore n'a pas à la connaître : une interface
  `OutboxStore` que le moteur implémente, ou une table xcore avec une connexion passée) ;
- après le commit, un répartiteur (le worker, ou un `@interval` court) prend les intentions prêtes, appelle la tâche, et enregistre
  l'état : `en attente` → `envoyé` / `échoué (n tentatives)`, avec un recul exponentiel et une file morte ;
- `key` rend l'opération idempotente : deux dépôts de la même clé = un seul envoi.

**Gain.** Plus de double envoi ni d'« envoyé sans document soumis ». La même brique sert aux notifications, aux webhooks et aux
intégrations — c'est-à-dire aux trois dernières cases du palier 70 % du moteur. Côté ERP, la limite connue du module `mail`
disparaît.

**Coût.** Moyen. L'essentiel est le contrat de dépôt (qui tient la transaction) ; le répartiteur est une boucle de 50 lignes au-dessus
de l'existant.

---

## 2. Ordonnanceur : le verrou ne doit pas dépendre de Redis

**Constat.** `SchedulerService` empêche les exécutions en double par un verrou Redis (`xcore:sched:lock:<job_id>`). Sans Redis —
c'est-à-dire avec le backend `memory`, le défaut —, **il n'y a aucun verrou** : chaque processus uvicorn lance la tâche. Le moteur
le documente comme un piège à éviter (`src/xcore_doctype/writer.py`) et s'en protège côté métier avec `ensure` (une création
rejouable). Une tâche de paie ou d'amortissement qui s'exécute deux fois n'a pas cette chance : elle écrit deux fois.

**Proposition.** Au choix, dans l'ordre de préférence :

1. un verrou utilisable sans Redis (un bail en base par `job_id`, avec une date d'expiration — la base est déjà là) ;
2. à défaut, un **refus explicite au démarrage** : `backend: memory` + plus d'un worker = erreur de configuration nommée, pas un
   démarrage silencieux qui doublera chaque tâche.

**Gain.** Le défaut cesse d'être dangereux. Aujourd'hui la configuration la plus simple est aussi la seule qui perd des données.

**Coût.** Faible. L'option 2 seule est une dizaine de lignes et vaut déjà la peine.

---

## 3. Services : un contrat déclaré, vérifié au chargement

**Constat.** `self.get_service("doctype_writer")` échoue à l'APPEL, pas au chargement. Un plugin qui attend un service absent (ou
dont la signature a changé) démarre, répond aux requêtes, et casse à la première écriture — en production, chez l'utilisateur. Dans
cette pile, le moteur exporte trois services (`doctype_registry`, `doctype_hooks`, `doctype_writer`) et l'ERP en dépend
entièrement ; rien ne le vérifie avant le premier clic.

**Proposition.** Dans le manifeste :

```yaml
requires_services:
  - name: doctype_writer
    version: ">=2"
```

et côté fournisseur un `provides_services: [{name: doctype_writer, version: "2.1"}]`. Au chargement, xcore refuse le plugin dont un
service requis manque ou dont la version ne satisfait pas la contrainte, avec un message qui nomme les deux plugins. `get_service`
peut rester tel quel : c'est le CHARGEMENT qui gagne une garantie.

**Gain.** Une incompatibilité se voit au démarrage, dans les logs de déploiement, et pas à la première facture. C'est le pendant de
ce que le moteur fait déjà pour les DocTypes (`requires` d'un module, vérifié par un test).

**Coût.** Faible à moyen (un champ de manifeste, un contrôle à l'ordre de chargement, qui existe déjà pour `requires`).

---

## 4. Clés de chiffrement : une par organisation, et une rotation

**Constat.** Le moteur scelle ses champs `Password` (`sealing.py`) avec UNE clé de processus. Dans un produit multi-organisation —
ce que cette pile est — toutes les organisations partagent donc la même clé : la fuite d'une clé est la fuite de tous les mots de
passe SMTP/IMAP de tous les clients, et il n'existe aucun chemin de rotation. Le réglage `Mail Settings` de l'ERP met exactement
cela en base, une fois par organisation.

**Proposition.** Un service `keys` :

- `keys.data_key(tenant_id, purpose)` rend la clé de chiffrement de l'organisation, dérivée d'une clé maîtresse (HKDF) ou lue dans
  un coffre (KMS, Vault) quand il est configuré ;
- un numéro de version par clé, exposé pour qu'un porteur de secret range `v<n>:<chiffré>` et qu'une rotation puisse relire
  l'ancien ;
- une politique de rotation déclenchable (une tâche qui réécrit les secrets d'une organisation sous la nouvelle version).

**Gain.** Le rayon d'une fuite se limite à une organisation, et la rotation devient possible — c'est une exigence habituelle de tout
audit, et un préalable à l'hébergement mutualisé.

**Coût.** Moyen, mais l'essentiel est la DÉRIVATION (quelques dizaines de lignes) ; le coffre externe peut rester une extension.

---

## 5. Dépendances d'un plugin : les déclarer, et le vérifier

**Constat.** Vu en vrai, dans le `pyproject.toml` de l'ERP : « Le moteur scelle ses champs Password avec `cryptography` sans le
déclarer : on le déclare ici. » Autrement dit un plugin importait un paquet que son manifeste ne nommait pas, et c'est
l'application en aval qui a dû réparer. Rien dans la chaîne ne l'a dit : ni l'installation, ni le chargement, ni la CI.

**Proposition.** Un contrôle livré avec xcore (`xcli plugin check`, et une étape de CI) qui compare les imports de haut niveau d'un
plugin aux dépendances déclarées (de son `pyproject` ou de son manifeste) et liste les manquantes. La forme compte peu — un
`importlib.metadata` + un parcours d'AST suffisent ; c'est l'EXISTENCE du contrôle qui manque.

**Gain.** Un `ImportError` à l'installation chez un client devient une erreur de CI chez l'auteur du plugin.

**Coût.** Faible.

---

## 6. Démarrage et empreinte du sandbox : une mesure venue de cette pile

**Constat.** Les tickets #305 et #306 nomment déjà le problème (imports celery/sqlalchemy pris au chargement, ~0,5 s et ~80 Mo par
worker sandbox, `Tracer._spans` non borné). Ce que cette pile ajoute, c'est un ORDRE DE GRANDEUR métier : un hôte ERP charge neuf
plugins de module, chacun avec ses DocTypes (704 définitions YAML au total) ; le coût d'import se paie par plugin et par worker, et
le temps de démarrage se voit à chaque rechargement en développement comme à chaque déploiement.

**Proposition.** Rien de nouveau : traiter #305 et #306, et y ajouter une mesure de référence reproductible (un hôte à neuf plugins)
pour que le gain soit chiffrable. Si une seule chose doit être faite : les imports paresseux de `celery` et `sqlalchemy`, qui sont
payés même par un plugin qui ne s'en sert pas.

---

## Ce qui n'est PAS demandé à xcore

Pour mémoire, parce que la frontière a déjà coûté des allers-retours (décision du 29 octobre 2026) : le moteur DocType et les
modules ERP ne demandent à xcore ni logique métier, ni stockage de documents, ni permissions par champ, ni impression. Le moteur
tient tout cela (`traoreera/doctype`, tickets #57 à #74). Les six points ci-dessus sont de l'INFRASTRUCTURE : transaction,
ordonnancement, services, clés, dépendances, démarrage.
