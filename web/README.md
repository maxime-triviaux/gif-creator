# App web Kaamelott GIF

La même chose que l'application de bureau du client, mais dans le navigateur, et surtout sur
iPhone : on cherche une réplique, les résultats s'affichent en grille de GIF animés, et un GIF
part dans Messages, WhatsApp ou Photos par la feuille de partage d'iOS.

C'est un service à part : une page statique servie par nginx, qui appelle l'API. L'API n'est pas
modifiée et ne connaît pas la page.

## Installer l'app sur un iPhone

1. Ouvre `https://gif.ml-sirh.fr/app/` dans **Safari**.
2. Touche **Partager**, puis **Sur l'écran d'accueil**, puis **Ajouter**.
3. Ouvre l'app depuis l'écran d'accueil, et saisis ta clé d'API.

Saisis la clé **dans l'app installée**, pas dans Safari : une app ajoutée à l'écran d'accueil a son
propre stockage, et ne voit pas ce qui a été tapé dans Safari. La page le rappelle tant qu'elle est
ouverte dans Safari.

Sur Android, Chrome propose **Installer l'application** dans son menu. Sur un ordinateur, la page
marche aussi dans le navigateur : le bouton **Partager** y devient **Télécharger**.

## Utilisation

- Tape une réplique puis **Chercher** : jusqu'à 12 résultats, en petits GIF de 240 px, générés
  2 par 2 (le serveur n'en génère que 2 à la fois).
- Touche une vignette : le GIF en 480 px est généré, puis **Partager** ouvre la feuille de partage.
  Seul le fichier est partagé, sans texte : avec un texte, certaines apps n'envoient que le texte.
- **Modifier le texte ou le passage** : remplace le texte incrusté, ou ajoute des secondes avant et
  après la réplique (0 à 5 s), puis **Regénérer**. Mêmes limites que l'API : 200 caractères,
  passage de 15 s au plus.
- **Clé**, en haut à droite : remplace la clé. Une clé refusée par l'API (révoquée, par exemple)
  ramène aussi à l'écran de saisie.

## La clé

- Elle est rangée dans le stockage local de l'appareil (`localStorage`), et n'est envoyée qu'à
  l'API, dans l'en-tête `X-API-Key`. Elle n'est ni dans le dépôt, ni dans l'image, ni sur le
  serveur de la page.
- Avant de l'enregistrer, la page la vérifie avec une recherche.
- La page n'utilise que ses propres fichiers (aucun script extérieur), et sa politique de sécurité
  (`Content-Security-Policy`) interdit d'en charger d'autres.
- Supprimer l'app de l'écran d'accueil efface la clé. Pour changer de clé sans la supprimer :
  bouton **Clé**.

## Pourquoi sous `/app` du domaine de l'API

Un navigateur n'envoie l'en-tête `X-API-Key` vers un autre domaine que si l'API l'autorise
(CORS), ce qu'elle ne fait pas. Sur le même domaine, la question ne se pose pas : Traefik envoie
`/app` à ce service, et tout le reste à l'API. Son routeur a une priorité plus haute que celui de
l'API, qui prend tout le domaine.

## Déployer

Le workflow [deploy-web.yml](../.github/workflows/deploy-web.yml) suit le même principe que
celui de l'API, et réutilise ses réglages GitHub : les secrets `VPS_HOST`, `VPS_USER`,
`VPS_SSH_KEY` et la variable `API_DOMAIN`. Rien d'autre à configurer, et aucune clé d'API.

Chaque push sur `main` qui touche `web/` lance le workflow, qui :

1. vérifie les fichiers compose et le JavaScript, construit l'image et teste la page (aussi sur
   chaque pull request) ;
2. déploie avec `docker compose up -d --build`, sans toucher au conteneur de l'API ;
3. vérifie que `https://API_DOMAIN/app/` sert la page, et que l'API répond toujours.

Pour un déploiement manuel : Actions → Deploy web → Run workflow. Depuis ton poste, avec le
contexte Docker `vps-perso` et un `web/.env` rempli (voir [.env.example](.env.example)) :

```bash
docker --context vps-perso compose --env-file web/.env -f web/docker-compose.yml -f web/docker-compose.traefik.yml up -d --build
```

Le projet compose s'appelle `kaamelott-gif-web`, et celui de l'API `kaamelott-gif` : le
`--remove-orphans` de l'un ne supprime jamais l'autre.

Après une mise à jour, l'app installée prend la nouvelle version à son lancement suivant.

## Lancer en local (développement)

La page doit être sur la même adresse que l'API : `docker-compose.dev.yml` fait relayer
`/repliques` par nginx vers une API lancée sur ton poste, comme Traefik sur le serveur.

1. Lance l'API en écoutant sur toutes les interfaces, pour que le conteneur la joigne (voir
   [api/README.md](../api/README.md#lancer-sans-docker-développement)), avec `--host 0.0.0.0` :

   ```bash
   DB_PATH=data/kaamelott-export.db MEDIA_DIR=data CACHE_DIR=/tmp/cache-gif API_KEYS=dev:cle-de-developpement \
     uvicorn app:app --app-dir api --host 0.0.0.0 --port 8000
   ```

2. Lance la page :

   ```bash
   docker compose -f web/docker-compose.yml -f web/docker-compose.dev.yml up --build
   ```

3. Ouvre `http://localhost:8080/app/`, avec la clé `cle-de-developpement`.

Le partage de fichiers et le service worker demandent HTTPS, sauf sur `localhost` : depuis un
iPhone sur le même réseau, la page s'ouvre mais **Partager** devient **Télécharger**. Pour tester
sur iPhone, déploie.

## Fichiers

| Fichier | Rôle |
|---|---|
| `static/index.html`, `app.js`, `style.css` | La page : clé, recherche, grille, partage |
| `static/manifest.webmanifest`, icônes | Nom, icône et plein écran une fois installée |
| `static/sw.js` | Garde les fichiers de la page, pour qu'elle s'ouvre même avec un réseau faible. Les requêtes à l'API ne passent pas par lui |
| `nginx.conf` | Sert `/app/`, avec les en-têtes de sécurité |
| `docker-compose.traefik.yml` | Routeur Traefik `/app` sur le domaine de l'API |
| `docker-compose.dev.yml`, `api-proxy.dev.conf` | Relais vers l'API du poste, en local |

Les icônes viennent de `client/icon.png`, recadrée sans marge : iOS et Android arrondissent les
coins eux-mêmes.
