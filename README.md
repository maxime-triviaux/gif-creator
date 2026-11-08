# Kaamelott GIF

Transcrit les épisodes de Kaamelott, permet de chercher une réplique et de l'exporter
en GIF avec le texte incrusté.

## How to start

Le dépôt ne contient que le code. Les vidéos, la base `data/kaamelott.db` et les GIF restent
en local : il faut fournir ses propres épisodes, puis construire la base.

### 1. Prérequis

Le projet est prévu pour macOS (Python 3.11 utilisé pour le développement).

```bash
brew install ffmpeg python
```

### 2. Récupérer le code et installer les dépendances

```bash
git clone <url-du-depot>
cd gif-creator
python3 -m venv .venv
source .venv/bin/activate
pip install pillow

# Mac Apple Silicon (M1/M2/M3/M4) :
pip install mlx-whisper
# Mac Intel :
pip install faster-whisper
```

### 3. Déposer les vidéos

Range les épisodes en `.mkv` dans un sous-dossier par Livre. `data/` est ignoré par Git.

```
data/Kaamelott/
├── S01/
│   ├── 001 Heat.mkv
│   └── 002 Les Tartes aux myrtilles.mkv
└── S02/
    └── ...
```

Si le nom du fichier contient `S01E05`, le Livre et le numéro en sont tirés. Sinon, le Livre
vient du dossier et le numéro de l'ordre des fichiers.

### 4. Construire la base

Teste d'abord sur 3 épisodes, depuis la racine du projet :

```bash
python cli/ingest.py data/Kaamelott --livre 1 --limite 3
```

Le premier lancement télécharge le modèle Whisper (~1,5 Go). Le temps par épisode affiché
permet d'estimer la durée totale. Lance ensuite toute la série :

```bash
caffeinate -i python cli/ingest.py data/Kaamelott
```

- `caffeinate -i` empêche le Mac de se mettre en veille pendant la nuit.
- Tu peux interrompre avec Ctrl-C : relance la même commande et ça reprend
  là où ça s'était arrêté (les épisodes déjà faits sont sautés).
- `--refaire` force la re-transcription, `--livre 3` limite à un Livre.

### 5. Chercher et exporter

```bash
python cli/search.py "c'est pas faux"
python cli/gif.py "c'est pas faux"
```

Lance ces commandes depuis la racine du projet, avec l'environnement activé
(`source .venv/bin/activate`). Les scripts trouvent la base `data/kaamelott.db` d'où qu'ils
soient lancés (`--db` pour en utiliser une autre), mais les GIF vont dans le dossier `output/`
du dossier courant.

## Transcrire des épisodes isolés

```bash
python cli/transcribe.py chemin/vers/heat.mkv --livre 1 --numero 5
```

Lancé depuis un dossier de vidéos, sans fichier en argument, le script transcrit tous les `.mkv`
de ce dossier, et les ajoute à la base du projet :

```bash
cd chemin/vers/videos
~/workspaces/claude/gif-creator/.venv/bin/python ~/workspaces/claude/gif-creator/cli/transcribe.py
```

Pas besoin de sous-dossiers `S01`, `S02`… Sans `SxxExx` dans le nom de fichier,
le Livre et le numéro viennent de `--livre` et `--numero` (défaut : Livre 0, ép. 1).

## Recherche

```bash
python cli/search.py "cest pas faux"      # apostrophes, accents, majuscules ignorés
python cli/search.py "graal" -n 50
python cli/search.py "graal" -o 2         # ouvre le résultat n° 2 au bon moment
```

`-o N` lance la vidéo dans VLC (ou `ffplay` si VLC n'est pas installé), 1 s avant la réplique.

Cherche d'abord la phrase exacte, sinon tous les mots dans n'importe quel ordre
(le dernier mot peut être incomplet : `assey` trouve « asseyez-vous »).

## Export en GIF

```bash
python cli/gif.py "radis"                 # menu ↑/↓ + Entrée s'il y a plusieurs résultats (q ou Échap : annuler)
python cli/gif.py "radis" -r 2            # exporte directement le 2e résultat
python cli/gif.py "radis" --apres 1.5 --texte "Comme des radis !" -o radis.gif
```

Les GIF sont écrits dans le dossier `output/`, créé au besoin, puis copiés dans le
presse-papier : il suffit de les coller (Cmd-V) dans une conversation ou le Finder.

Options utiles : `--avant` et `--apres` pour recaler le passage, `--largeur` et `--fps`
pour alléger le fichier (défaut : 480 px, 12 images/s).

## API REST (Docker)

`api/` expose la recherche et l'export en GIF sous forme d'API, pour un serveur.
Installation et déploiement : [api/README.md](api/README.md).

## Client à distribuer

`client/kaamelott-gif-cli.py` fait la même chose que `cli/gif.py`, mais en passant par l'API : ceux
qui l'utilisent n'ont besoin ni des vidéos ni de la base, juste de Python 3 et d'une clé dans un
`.env`. Voir [client/README.md](client/README.md).

## App web (iPhone)

`web/` est une app web installable sur l'écran d'accueil d'un iPhone : recherche, grille de GIF et
partage par la feuille de partage d'iOS. Chacun y saisit sa clé, qui reste sur son téléphone. Elle
est servie sous `https://API_DOMAIN/app/`, sans modifier l'API. Voir [web/README.md](web/README.md).

## Ce qui est stocké (`data/kaamelott.db`)

- `episodes` : Livre, numéro, titre (tiré du nom de fichier `SxxExx - Titre.mkv`), chemin de la
  vidéo relatif à `data/` (`Kaamelott/S01/…`) : la base reste valable si le projet est déplacé.
  Une vidéo transcrite hors de `data/` garde son chemin absolu.
- `cues` : chaque réplique avec début/fin en secondes
- `mots` : horodatage de chaque mot (pour caler les GIF au plus près)
- `cues_fts` : index de recherche plein texte
