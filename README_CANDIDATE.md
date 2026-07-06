# Test technique Primmo

Ce repo contient le code d'une API multi-tenant qui ingère un document, le fait passer dans un pipeline de traitement asynchrone (OCR / metadata et chunking / appel partenaire externe), et laisse le client suivre l'avancement en temps réel jusqu'à ce que le document soit `ready`.

**Cas d'usage partenaire (3 lignes).** Le step `external_call` envoie le document enrichi (texte OCR + métadonnées + chunks) à un moteur d'indexation documentaire externe. Ce partenaire indexe de façon asynchrone puis rappelle notre API via un webhook signé quand l'indexation aboutit (ou échoue). Le pipeline construit ne bloque : il dispatche puis reprend sur le callback.

---

## Démarrage

```bash
docker compose up --build
```

Le fichier compose monte les services suivants : `postgres`, `redis`, `migrate` (one-off), `seed` (one off), `api` (uvicorn), `worker` (Celery).

Le swagger est accessible à l'URL : **http://localhost:8000/docs**.

**Seed** (UUIDs fixes) :

| Organisation | tenant_id | user_id |
|---|---|---|
| Acme | `00000000-0000-0000-0000-000000000001` | `00000000-0000-0000-0000-000000000011` |
| Beta | `00000000-0000-0000-0000-000000000002` | `00000000-0000-0000-0000-000000000022` |

Chaque tenant n'a qu'un seul user.

**Flux complet depuis `/docs`** :
1. `GET /auth/dev-token?tenant_id=…&user_id=…` => `access_token`. **Authorize** dans Swagger (Bearer).
2. `POST /documents` (upload un fichier) => `document_id`, `status: processing`.
3. `GET /documents/{id}` => suivre le statut ; quand `external_call` a délégué, le champ `partner_job_id` apparaît dans le payload.
4. Simuler le webhook partenaire : `POST /dev/sign` avec le body ci-dessous afin d'obtenir la `signature` ; puis appeler `POST /webhooks/partner` avec ce même body et le header `X-Partner-Signature: <signature>`.
   ```json
   { "job_id": "<partner_job_id>", "status": "completed", "result": { "indexed_at": "2026-01-01T00:00:00Z" } }
   ```
5. `GET /documents/{id}` => `status: ready` ; `GET /documents/{id}/results` => retourne toutes les données extraites (tous les steps).
6. Temps réel : `GET /documents/{id}/events?token=<jwt>` (SSE).

Le secret HMAC partagé hors-bande est `PARTNER_HMAC_SECRET` (variable d'env, cf. `docker-compose.yml`). `/dev/sign` (gaté par `DEV_MODE`) permet de calculer une signature valide depuis Swagger.

---

## Architecture

Architecture hexagonale (domaine pur, use cases applicatifs, ports, adapters) : le domaine n'importe ni FastAPI ni SQLAlchemy. La logique du domaine est testée au moyen de test doubles.

**Vocabulaire.** Un `Document` (1:1 avec un `Workflow`, même UUID -- on fait l'hypothèse que si l'utilisateur voulait retenter le workflow complet, il ferait un nouvel upload de document) traverse un DAG de `Step` (nœuds structurels). Chaque `Step` exécuté devient une `Task` (stocke les retries, le statut, l'historique). Chaque try/retry de `Task` est appelé `TaskInstance`. Les workers exécutent directement le code d'orchestration (mêmes ports Postgres/Redis que l'API), pas via un callback HTTP vers l'API.

---

## Décisions clés (au regard de la cible : ~100k docs/j, 5000 users concurrents, p95 pipeline < 2 min, et plus généralement au regard de la lisibilité et de l'évolutivité de la code base)

**Persistance : Postgres, SQLAlchemy synchrone.** Un seul chemin de session/UoW partagé entre l'API (endpoints `def` dans le threadpool) et les workers Celery (synchrones - convient bien aujourd'hui, mais pourrait se révéler inadapté en cas de jobs très IO bound). La `WorkflowDefinition` est sérialisée en JSONB (auto-descriptive, versioning-safe) : OK pour l'instant, mais les définitions de workflows pourraient à l'avenir être persistées ailleurs (table à part, blob store) pour 1) éviter une duplication inutile en base et 2) pour permettre le versioning du DAG. Les résultats des steps (texte OCRisé, chunks volumineux) vivent dans un **BlobStore** ; seule la clé transite en base/broker/SSE.

**Orchestration async : Celery + Redis, une task par step, pas d'orchestrateur central.** Le broker Redis suffit (pas de result backend) : chaque task enqueue elle-même ses successeurs (`run_pipeline_step`), le DAG s'auto-propage, y compris depuis le chemin webhook. L'ensemble peut être mis à l'échelle en ajoutant des workers.

**Dimensionnement (ordre de grandeur).** Les tâches mockées dorment (23 secondes de travail cumulé par document en moyenne) et échouent 1 fois sur 3 => ~1,5 tentative par step => ~35 worker-secondes par document => à 100 000 docs/jour : 3,5 millions de worker-secondes ≈ **~40 slots workers occupés en moyenne** (1j. = 86400 secondes). Par conséquent, utiliser 3 workers  avec `--concurrency=16` suffit a priori, mais il faut surveiller les pics d'activité en journée et auquel cas multiplier le nombre de workers. La métrique principale à surveiller est la profondeur de queue.

**Fan-in / optimistic concurrency.** Afin de gérer le fan in des tâches `metadata` et `chunking` qui tournent en parallèle et pourraient finir au même moment, on utilise de l'optimistic concurrency. Une colonne `version` est ajoutée sur `workflows` et on utilise classiquement `UPDATE … WHERE version=v` lorsqu'on veut mettre à jour l'objet Workflow : la branche perdante lève `ConcurrencyError`, est autorisée à réessayer à partir d'un état mis à jour (avec un maximum -- voir améliorations possibles plus bas). Grâce à cela, seul le second committer voit les deux tâches parallèles finalisées et peut dispatcher `external_call`.

**Temps réel : SSE + Redis pub/sub (pas de WebSocket ni de polling en continu).** Le besoin est unidirectionnel (serveur => client) donc WebSocket est surdimensionné. Par ailleurs, on anticipe 5,000 users concurrents donc un polling en continu générerait 5000 requetes par seconde (pour respecter la contrainte de réactivité) qui la plupart du temps ne servent à rien vu le temps nécessaire typiquement pour finaliser une tâche. Par ailleurs, SSE a des avantages : reconnexion native, traverse les proxies. Chaque event publié porte une version monotone pour que le client ignore doublons ou ordre inverse.

**Isolation tenant : RLS Postgres + scoping applicatif.** Chaque requête filtre déjà par `tenant_id` (de façon applicative), mais RLS ajoute également la garantie au niveau de la DB : une policy ne laisse voir/écrire une ligne que si le GUC de session vaut son `tenant_id` (pour les use cases où l'isolation est nécessaire, ce qui n'est pas le cas de tous les use cases -- lorsque l'on reçoit un webhook du partenaire, on doit faire un scan multi-tenant, il faut alors un bypass). Le scoping est factorisé dans `WriteUseCase` (classe mère pour tous les use cases mono-tenant) : un use case ne peut donc pas l'oublier. Un utilisateur non superuser a été créé et utilisé dans l'API et le worker afin de ne pas contourner ces restrictions.

**Sécurité webhook.** HMAC-SHA256 vérifié sur les octets bruts avant tout parsing (re-sérialiser casserait la signature). La taille du body est bornée (64 Ko) avant le calcul HMAC (précaution anti-DoS).

**Résilience**

Côté pipeline (tasks lancées par l'ingestion, `run_pipeline_step`) :
- **Un step échoue (exception)** — cas nominal (1 fois sur 3) : absorbé par le budget de retries de la `Task` (`max_attempts`).
- **Un worker Celery crashe en plein step** : Celery acquitte le message à la réception (at-most-once), donc le step en cours est perdu — la `Task` reste `RUNNING`, le workflow est figé. C'est une limite assumée ; le filet est le job de réconciliation, et le vrai correctif (`acks_late`) est décrit dans « avec plus de temps » (voir plus bas).

Côté webhook (task `apply_partner_callback`) : le partenaire re-livre sur non-2xx : « appliquer le callback » sera donc appelé plusieurs fois par construction. Le endpoint ne fait que valider et enqueuer (`202`, aucun travail transactionnel dans la requête du partenaire) ; l'application se fait dans un worker, et elle est idempotente à deux niveaux : une `Task` déjà terminale est un no-op (et est acquittée avec `200 already_processed`) ; dans le cas de deux livraisons simultanées, le verrou optimiste (`version`) fait échouer la perdante, qui rejoue sur l'état frais et retombe sur le no-op. Résultat : application exactement une fois, quel que soit le nombre et l'ordre des livraisons. En revanche, la tâche en elle-même doit être idempotente puisqu'elle peut être rejouée deux fois dans ce dernier cas.

**Échec transitoire vs échec terminal.** Il y a deux natures d'échec dans le code. L'échec transitoire est absorbé par le budget de retries de la `Task` (`max_attempts`) et ne remonte jamais au `Workflow`. L'échec terminal (budget épuisé, ou échec déclaré définitif par le partenaire) marque la `Task` et le `Workflow` `failed` (avec `failed_step` / `failure_reason`).

**Persistance des résultats des Tasks** Les résultats des tasks (outputs) sont enregistrés dans le blob store. Cela permettra à terme de ne pasavoir à renvoyer les résultats (qui peuvent être potentiellement volumineux), mais seulement un lien de download signé vers les résultats afin de les faire télécharger par le client seulement et ainsi décharger l'API.

**Workflow definition séparé de Workflow** : cela permet à un workflow de se consacrer sur l'orchestration uniquement, et non sur les détails métier d'un pipeline.

**Fonctionnalités accès DB haut niveau réservées aux mutations**: dans le cas d'un use case de lecture, on utilise des requêtes simple : pas d'utilisation d'une unit of work notamment.

---

## Tests

Note : la création d'un environnement virtuel (si pertinent) et l'installation des dépendances est laissée au choix du reviewer.

- **Suite unitaire rapide** (test doubles, sans I/O) : domaine, use cases, endpoints. Pour exécuter ces tests : `pytest`.
- **Suite d'intégration** (testcontainers : vrai Postgres + Redis) : RLS (isolation, `WITH CHECK`, fail-closed, bypass), optimistic concurrency, round-trip domaine↔ORM, Redis pub/sub, requêtes data sources. Pour exécuter ces tests : `pytest -m integration`.

## Limites assumées & avec plus de temps

**Limites assumées.**
* L'idempotence webhook est assurée par la garantie que la tâche liée ne peut passer qu'une seule fois à un état final
* Le snapshot SSE qui est envoyé à la connexion SSE couvre l'état à la (re)connexion mais pas les transitions manquées pendant une coupure
* La pagination des documents est effectuée avec un `offset/limit`, peut être coûteux
* Un crash du worker entre le retour d'`external_call()` et le commit perd le job_id généré par le partenaire ; par conséquent, tous les appels webhooks du partenaire renverront 404 pour ce job_id (cela peut être détectable par un job de réconciliation -- voir ci-dessous)
* Le endpoint d'ingestion de documents est async, mais l'exécution du use case bloque l'event loop (car accès blob, Redis et DB synchrone). Cela pourrait introduire des lenteurs en cas de blocage réseau pour l'un des accès externes
* Les messages SSE étant envoyés pendant l'exécution du use case, on pourrait avoir des messages "fantômes", i.e. déjà envoyés aux consommateurs alors que la transaction DB subséquente échoue.
* Avec 5000 clients qui écoutent les messages SSE en même temps, cela représente 5000 connexions Redis simultanées. Encore OK, mais ne tiendra pas pour un ordre de grandeur plus grand.

**Qu'aurait-on pu faire avec plus de temps ?**
* Transactional outbox : permet d'assurer la corrélation entre dispatch des tâches et évolution de l'objet Workflow
* Evénements de domaine first-class : les agrégats enregistrent leurs événements (DocumentCreated, StepSucceeded, etc.), publiés en un seul point après commit : cela permettrait de supprimer les events fantômes côté SSE, et de découpler l'ingestion du déclenchement du pipeline, et cela serait une première étape dans l'implémentation du pattern transactional outbox cité plus haut.
* Utiliser Redis Streams pour un SSE sans perte de message à la reconnexion
* Pagination via keyset et non via offset
* Garbage collection des blobs orphelins
* Utiliser un "vrai" Blob Store (S3 par exemple) 
* Configuration Celery différenciée par step (queues/priorités)
* Refactor des dispatchers, sur deux axes.
  - **Renommer `PartnerCallbackDispatcher` en `DeferredResultDispatcher`** : ce qu'il dispatche, ce n'est pas « un callback du partenaire », c'est le résultat d'un step différé. Seul le endpoint exposant le webhook a besoin de connaître le partenaire (HMAC, forme du payload) ; en dessous, le mécanisme est générique — un futur step différé d'un autre type (validation humaine, autre prestataire) le réutiliserait tel quel.
  - **Factoriser le dispatch des steps.** Le dispatch de steps prêts d'un workflow est écrit à trois endroits : à l'ingestion (`CeleryWorkflowDispatcher` lance les steps racines pour démarrer la chaîne), et après chaque step réussi (fin de `run_pipeline_step`, fin de la task webhook où on dispatche ce que le step vient de débloquer). Un port unique `StepDispatcher.dispatch_steps(...)` remplacerait les trois.
* Ne surtout pas garder l'auth "maison". Le token actuel n'existe que pour rendre le test exécutable de bout en bout — ce n'est en aucun cas une cible de production. En conditions réelles, on déléguerait à un système d'identité managé plutôt que de réimplémenter à la main l'émission et la validation des tokens.
* Cookie `HttpOnly` pour le token SSE (afin de ne pas le faire apparaître dans l'URL)
* Check lint, mypy et tests via un hook pre-commit (CI exclue du scope du projet)
* Ne plus perdre de step sur un crash worker : passer Celery en `acks_late`. Aujourd'hui le message est acquitté *à la réception* : si le worker meurt en plein step, le message a déjà disparu de la queue, personne ne le rejouera. Avec `acks_late`, le message n'est acquitté qu'*à la fin* du traitement : un worker qui meurt laisse le message dans la queue, le broker le re-livre à un autre worker, et le step finit toujours par s'exécuter. Le prix : le même step peut alors être exécuté **deux fois** (le message est re-livré alors que le premier worker avait peut-être déjà fait une partie du travail — voire tourne encore, si c'est un faux positif). Il faut donc que rejouer soit sans danger, ce qui demande deux mécanismes :
  - **Reprise sur état** : au démarrage, la task relit l'état en base et reprend là où les choses en sont resté, au lieu de tout refaire aveuglément. Exemple : si la `Task` est déjà `SUCCEEDED` (le crash a eu lieu *après* le commit du résultat mais *avant* l'enqueue des steps suivants), on ne ré-exécute pas la fonction du step — on se contente de re-dispatcher les steps débloqués.
  - **Lease (un « bail » sur la Task)** : une `Task` `RUNNING` porterait une date d'expiration (« un worker travaille dessus jusqu'à T »). Une re-livraison qui trouve la Task `RUNNING` consulte le bail : encore valide => un autre worker est réellement en train de travailler, on ne touche à rien (protège du double travail) ; expiré => le worker est présumé mort, on reprend la main. Sans ce bail, impossible de distinguer « en cours d'exécution » de « abandonné par un crash ».
* Jobs de réconciliation, afin de détecter et réparer des états qui ne peuvent plus progresser seuls. Exemples de cas concrets : (a) une `Task` `external_call` RUNNING avec `partner_job_id` NULL depuis > N minutes => le job_id partenaire a probablement été perdu (crash entre l'appel sortant et son commit) et ne pourra jamais être terminée par la réception du webhook associé ; (b) toute `Task` RUNNING depuis anormalement longtemps => cela veut dire que le worker a crashé mid-step ou bien que le message a été perdu ; (c) un `Workflow` RUNNING sans aucune `Task` active ni step prêt dispatché => le dispatch a été perdu entre le commit et le `.delay()`.

---

## Stack & structure

```
src/
  domain/            # entités + règles, aucun import framework
  application/       # use cases, orchestrateur, unit of work
  ports/             # interfaces (BlobStore, EventPublisher, repositories…)
  adapters/          # sql/ · redis/ · celery/ · filesystem/ · in_memory/
  routers/           # endpoints FastAPI
tests/               # unitaires (doubles) + integration/ (testcontainers)
```

---

## Méthode de travail

Le code de ce repository a été en grande partie écrit par Claude Code, sous ma supervision. Voici le processus qui a été utilisé :
* Etude des contraintes techniques et des fonctionnalités à implémenter ;
* Design de l'architecture de la solution (choix, trade-offs maintenant vs plus tard) ;
* Découpage du travail en macro tâches logiques, ordonnancement des tâches (exemple : le domaine d'abord) ;
* Pour chaque macro tâche, découpage en micro-tâches et choix techniques de plus bas niveau ;
* Implémentation de chaque micro tâche ;
* Revue de l'implémentation, challenge, questions ;
* Tests manuels sur le process E2E (dès que celui-ci était disponible).