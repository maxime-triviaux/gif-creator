# Client Kaamelott GIF

Cherche une réplique et l'exporte en GIF à travers l'API : pas besoin des vidéos ni de la base en
local. Un seul fichier, qui n'utilise que la bibliothèque standard de Python 3 : rien à installer.

## Installation

1. Récupère `kaamelott-gif.py` et `.env.example`, dans le même dossier.
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
python3 kaamelott-gif.py "c'est pas faux"            # menu ↑/↓ + Entrée s'il y a plusieurs résultats
python3 kaamelott-gif.py "graal" -l                  # liste les résultats, sans export
python3 kaamelott-gif.py "graal" -r 2                # exporte directement le 2e résultat
python3 kaamelott-gif.py "radis" --apres 1.5 --texte "Comme des radis !" -o radis.gif
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
