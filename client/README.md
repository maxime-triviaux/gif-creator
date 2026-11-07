# Client Kaamelott GIF

Cherche une réplique et l'exporte en GIF à travers l'API : pas besoin des vidéos ni de la base en
local. Un seul fichier, qui n'utilise que la bibliothèque standard de Python 3 : rien à installer.

## Installation

1. Récupère `kaamelott-gif-cli.py` et `.env.example`, dans le même dossier.
2. Copie `.env.example` en `.env`, et mets-y la clé d'API qu'on t'a donnée :

   ```
   KAAMELOTT_API_KEY=ta-cle
   ```

Il faut Python 3.8 ou plus récent. Sur macOS, `python3` est déjà là (sinon, macOS propose de
l'installer au premier lancement). Avec le Python de python.org, qui n'a pas de certificats
racines tant que « Install Certificates.command » n'a pas été lancé, le script utilise ceux du
trousseau de macOS.

Le script cherche la clé dans la variable d'environnement `KAAMELOTT_API_KEY`, puis dans le `.env`
du dossier courant, puis dans le `.env` à côté du script. Ne partage pas ton `.env` : la clé est
personnelle, et chaque requête est enregistrée à ton nom.

## Utilisation

```bash
python3 kaamelott-gif-cli.py "c'est pas faux"            # menu ↑/↓ + Entrée s'il y a plusieurs résultats
python3 kaamelott-gif-cli.py "graal" -l                  # liste les résultats, sans export
python3 kaamelott-gif-cli.py "graal" -r 2                # exporte directement le 2e résultat
python3 kaamelott-gif-cli.py "radis" --apres 1.5 --texte "Comme des radis !" -o radis.gif
```

Le GIF est écrit dans le dossier `output/` du dossier courant, puis copié dans le presse-papier
(macOS, ou Linux avec `wl-copy` ou `xclip`) : il suffit de le coller dans une conversation.

| Option | Défaut | Limites |
|---|---|---|
| `-n` | 15 résultats | 50 |
| `--avant`, `--apres` | 0.2 et 0.4 s | 0 à 5 s |
| `--largeur` | 480 px | 160 à 720 |
| `--fps` | 12 | 5 à 20 |
| `--texte` | réplique transcrite | 200 caractères |

Le passage complet ne peut pas dépasser 15 s. Le premier export d'un passage prend quelques
secondes (le serveur le génère), les suivants sont immédiats.

Sous Windows, le menu à flèches est remplacé par une liste numérotée.

## Application de bureau

`kaamelott-gif-app.py` affiche les résultats sous forme de grille de GIF animés. Elle utilise
le même `.env`, et a besoin de `kaamelott-gif-cli.py` et `icon.png` dans le même dossier.

Elle nécessite PySide6 (Qt), à installer une fois dans un environnement virtuel, depuis le
dossier du client :

```bash
python3 -m venv .venv
```
```bash
.venv/bin/pip install -r requirements-app.txt
```

Puis, pour la lancer :

```bash
.venv/bin/python kaamelott-gif-app.py
```

- Tape une réplique puis Entrée : jusqu'à 12 résultats s'affichent, en petits GIF de 240 px.
  Ils sont générés 2 par 2, car le serveur n'en génère que 2 à la fois.
- Survole un GIF : il se partage en deux moitiés. À gauche, l'icône **copier** met le GIF animé
  dans le presse-papier ; à droite, l'icône **enregistrer** demande où l'écrire (Téléchargements
  par défaut). Une coche confirme l'action.
- Un clic ailleurs sur la vignette (le texte, l'épisode) copie aussi le GIF.
- Le GIF copié ou enregistré fait 480 px de large, avec les réglages par défaut de la CLI.
  Pour changer le texte ou le passage, utilise `kaamelott-gif-cli.py` (`--texte`, `--avant`,
  `--apres`, `--largeur`).
- Les GIF copiés restent un jour dans le cache de l'application, puis sont supprimés.

Dans le Dock, l'application prend l'icône `icon.png` (un Graal, à remplacer par n'importe quelle
image carrée). Lancée comme script, elle garde le nom « Python » dans la barre de menus de macOS.

### Installer l'application dans le Dock (macOS)

`build-app.sh` construit une vraie application macOS, « Kaamelott GIF.app », avec son icône et son
nom. Depuis la racine du dépôt, avec ta clé dans `client/.env` :

```bash
client/build-app.sh
```
```bash
cp -R "client/dist/Kaamelott GIF.app" /Applications/
```

Lance-la depuis le dossier Applications, puis fais un clic droit sur son icône dans le Dock, et
choisis **Options → Garder dans le Dock**.

- Le script installe PySide6 et PyInstaller dans `client/.venv` s'ils manquent, et produit une
  application d'environ 100 Mo (elle embarque Python et Qt).
- **La clé de `client/.env` est copiée dans l'application.** Ne partage pas le `.app` : chacun
  construit le sien avec sa clé. `client/dist/` est ignoré par Git.
- Après un changement de clé ou de code, relance `build-app.sh` puis recopie l'application.
- L'application n'est pas signée. Construite sur ton Mac, elle s'ouvre normalement ; copiée
  depuis un autre Mac, macOS demande de l'ouvrir une première fois par clic droit → Ouvrir.
