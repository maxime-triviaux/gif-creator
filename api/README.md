# API Kaamelott GIF

API REST qui expose la recherche de répliques et l'export en GIF des scripts de `cli/`.
Elle tourne dans Docker et lit la base et les vidéos depuis des dossiers de la machine hôte,
en lecture seule. L'indexation reste sur ton poste, avec `cli/ingest.py`.

## Endpoints

Chaque requête doit envoyer une clé dans l'en-tête `X-API-Key` (voir [Gérer les clés](#gérer-les-clés)).
La documentation interactive est sur `/docs`.

### `GET /repliques?q=...&limit=15`

Cherche une réplique (`limit` : 1 à 50). Renvoie une liste JSON :

```json
[{"id": 33, "livre": 1, "numero": 1, "titre": "001    Heat", "debut": 55.66, "fin": 57.24,
  "texte": "On va pas rester plantés là comme des radis !",
  "avant": ["Ils sont encore là, ces cons !"], "apres": ["Si un autre groupe…"],
  "gif": "/repliques/33/gif"}]
```

### `GET /repliques/{id}/gif`

Renvoie le GIF (`image/gif`) de la réplique `id` trouvée par la recherche. Le premier appel
le génère, les suivants le ressertent depuis le cache.

| Paramètre | Défaut | Limites |
|---|---|---|
| `texte` | texte transcrit | 200 caractères |
| `avant`, `apres` | 0.2 et 0.4 s | 0 à 5 s |
| `largeur` | 480 px | 160 à 720 |
| `fps` | 12 | 5 à 20 |

Le passage complet ne peut pas dépasser 15 s. Les `id` changent quand un épisode est
re-transcrit : refais alors la recherche.

```bash
curl -H "X-API-Key: $CLE" "http://localhost:8000/repliques?q=radis"
curl -H "X-API-Key: $CLE" -o radis.gif "http://localhost:8000/repliques/33/gif?apres=1"
```

## Déployer sur la machine OVH

### 1. Préparer les dossiers sur le serveur

```bash
ssh user@serveur 'sudo mkdir -p /srv/kaamelott/db /srv/kaamelott/medias && sudo chown -R $USER /srv/kaamelott'
```

### 2. Envoyer les vidéos

Le dossier des médias du serveur joue le rôle de ton `data/` : il doit contenir `Kaamelott/S01`,
`Kaamelott/S02`… (24 Go : compte du temps). Sans `/` après `data/Kaamelott`, `rsync` copie le
dossier lui-même :

```bash
rsync -a --partial --progress data/Kaamelott user@serveur:/srv/kaamelott/medias/
```

Si la copie est coupée, relance la même commande : `rsync` reprend là où il s'est arrêté.

### 3. Exporter et envoyer la base

La base de ton poste (`data/kaamelott.db`) est en mode WAL : une copie brute est illisible en
lecture seule, et peut manquer les dernières répliques. Exporte une copie propre, depuis la racine
du projet :

```bash
rm -f data/kaamelott-export.db
sqlite3 data/kaamelott.db "VACUUM INTO 'data/kaamelott-export.db'"
```

Envoie-la sous un nom temporaire, puis renomme-la : l'API ne lit jamais une base à moitié copiée,
et voit la nouvelle base dès la requête suivante, sans redémarrage.

```bash
scp data/kaamelott-export.db user@serveur:/srv/kaamelott/db/.envoi.db
ssh user@serveur 'mv /srv/kaamelott/db/.envoi.db /srv/kaamelott/db/kaamelott.db'
```

Refais cette étape après chaque nouvelle indexation.

Le conteneur tourne avec un utilisateur non-root : les vidéos et la base doivent être lisibles par
tous sur le serveur (fichiers en 644, dossiers en 755, ce que `scp` produit d'habitude).

### 4. Pointer le domaine vers le VPS

Crée un enregistrement DNS `A` (par exemple `gif.ml-sirh.fr`) vers l'IP du VPS. Traefik obtient
le certificat Let's Encrypt au premier déploiement.

### 5. Configurer GitHub

Le workflow [deploy-api.yml](../.github/workflows/deploy-api.yml) reprend le principe du site
(dépôt `ml-sirh`) : `docker compose` pilote le Docker du VPS par SSH, l'image est construite sur le
serveur, et le Traefik du dossier `traefik/` la publie en HTTPS grâce aux labels de
[docker-compose.traefik.yml](docker-compose.traefik.yml). Rien n'est à cloner sur le serveur.

Les secrets ne sont pas partagés entre dépôts : il faut les recréer dans `gif-creator`
(Settings → Secrets and variables → Actions), ou avec `gh` depuis la racine du dépôt.

| Secret | Valeur |
|---|---|
| `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` | Les mêmes que dans `ml-sirh` |
| `API_KEYS` | Une clé par personne : `alice:cle1,bob:cle2` (voir [Gérer les clés](#gérer-les-clés)) |

| Variable | Exemple |
|---|---|
| `API_DOMAIN` | `gif.ml-sirh.fr` |
| `HOST_DB_DIR` | `/srv/kaamelott/db` |
| `HOST_MEDIA_DIR` | `/srv/kaamelott/medias` |
| `CACHE_MAX_MO`, `MAX_FFMPEG` | Facultatives (voir [.env.example](.env.example)) |

```bash
gh secret set VPS_SSH_KEY < ~/.ssh/cle_de_deploiement
gh variable set API_DOMAIN --body gif.ml-sirh.fr
```

### 6. Déployer

Chaque push sur `main` qui touche l'API (`api/`, `cli/db.py`, `cli/gif.py`, `cli/search.py`)
lance le workflow, qui :

1. construit l'image et vérifie que l'API démarre (aussi sur chaque pull request) ;
2. vérifie que la base et les vidéos sont bien sur le VPS ;
3. déploie avec `docker compose up -d --build` ;
4. vérifie que `https://API_DOMAIN` répond, avec la première clé de `API_KEYS`.

Pour un déploiement manuel : Actions → Deploy API → Run workflow. Depuis ton poste, avec le
contexte Docker `vps-perso` et un `api/.env` rempli :

```bash
docker --context vps-perso compose --env-file api/.env -f api/docker-compose.yml -f api/docker-compose.traefik.yml up -d --build
```

### Chemins des vidéos

La base repère chaque vidéo par son chemin dans `data/` (`Kaamelott/S01/001    Heat.mkv`), et
l'API le cherche au même endroit dans `HOST_MEDIA_DIR`. Une vidéo transcrite hors de `data/`
(avec `cli/transcribe.py`) garde un chemin absolu et ne peut pas être servie.

### Accès

L'API est publique à l'adresse `https://API_DOMAIN`, protégée par les clés. Le port 8000 n'est
ouvert que sur le VPS lui-même (`BIND=127.0.0.1`), pour le débogage. Ne distribue les clés qu'à
des proches : l'API diffuse des extraits d'une œuvre protégée.

## Gérer les clés

Chaque personne a sa propre clé, et toutes sont dans le secret GitHub `API_KEYS`, au format
`nom:clé`, séparées par des virgules :

```
alice:3f9c…,bob:8a21…
```

- **Nom :** lettres, chiffres, `_ . @ -`. Il apparaît dans le journal de chaque requête.
- **Clé :** 16 caractères au moins. Pour en générer une : `openssl rand -hex 24`.
- **Ordre :** le workflow vérifie le déploiement avec la première clé de la liste.

L'API refuse de démarrer si la liste est vide ou mal formée (nom invalide, nom ou clé en double,
clé trop courte), et le journal en donne la raison, sans jamais citer de clé.

### Ajouter ou révoquer une clé

GitHub ne réaffiche jamais un secret : garde la liste complète de ton côté, par exemple dans ton
gestionnaire de mots de passe. Pour la changer, remplace le secret en entier, puis redéploie :

```bash
gh secret set API_KEYS
```
```bash
gh workflow run deploy-api.yml
```

`gh secret set` sans `--body` demande la valeur sans l'écrire dans l'historique du terminal.
Le redéploiement prend environ une minute ; jusque-là, les anciennes clés restent valables.

### Savoir qui utilise l'API

Chaque requête est journalisée avec le nom de la clé, et les clés refusées aussi :

```
INFO:     alice : GET /repliques?q=graal&limit=1
INFO:     alice : GET /repliques/33/gif?texte=Coucou
WARNING:  clé inconnue refusée : GET /repliques?q=graal&limit=1
```

```bash
docker --context vps-perso logs kaamelott-gif-api-1 2>&1 | grep 'alice :'
```

## Mettre à jour

- **Nouvelle base :** étape 3, sans redémarrage.
- **Nouveau code :** push sur `main`, le workflow redéploie.
- **Rendu modifié** (police, filtres) : incrémente `RENDER_VERSION` dans `api/app.py`, sinon le
  cache continue de servir les anciens GIF.

## Cache

Les GIF sont gardés dans le volume Docker `cache`, jusqu'à `CACHE_MAX_MO` (2000 Mo par défaut).
Au-delà, les moins récemment servis sont supprimés. Le volume peut être vidé sans risque :
tout se régénère. `MAX_FFMPEG` (2 par défaut) limite les générations simultanées ; au-delà,
une requête attend jusqu'à 30 s, puis reçoit `503`.

## Lancer sans Docker (développement)

```bash
python3 -m venv .venv-api && source .venv-api/bin/activate
pip install -r api/requirements.txt
DB_PATH=data/kaamelott-export.db MEDIA_DIR=data CACHE_DIR=/tmp/cache-gif API_KEYS=dev:cle-de-developpement \
  uvicorn app:app --app-dir api --port 8000
```
