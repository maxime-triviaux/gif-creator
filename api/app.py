"""API REST : recherche de répliques et export en GIF.

Réutilise la recherche (cli/db.py) et le rendu des GIF (cli/gif.py) des scripts en ligne de commande.
La base est ouverte en lecture seule : l'indexation se fait ailleurs, avec cli/ingest.py.
"""
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.security import APIKeyHeader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
from db import chemin_video as chemin_dans, rechercher  # noqa: E402
from gif import dessiner_texte, exporter, nom_par_defaut  # noqa: E402

DB_PATH = Path(os.environ.get("DB_PATH", "/data/kaamelott.db"))
# Équivalent du dossier data/ du poste : la base y repère les vidéos par « Kaamelott/S01/… »
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/cache"))
CACHE_MAX_OCTETS = int(os.environ.get("CACHE_MAX_MO", "2000")) * 1_000_000
MAX_FFMPEG = int(os.environ.get("MAX_FFMPEG", "2"))
# Une clé par personne : « alice:cle1,bob:cle2 ». Le nom apparaît dans le journal de chaque requête.
API_KEYS = os.environ.get("API_KEYS", "")

VERSION_RENDU = 1  # à incrémenter quand le rendu change : les anciens GIF du cache sont ignorés
DUREE_MAX = 15.0   # secondes de vidéo par GIF
ATTENTE_MAX = 30   # secondes d'attente d'une place libre pour ffmpeg
DELAI_FFMPEG = 120

generations = threading.BoundedSemaphore(MAX_FFMPEG)
verrous = [threading.Lock() for _ in range(64)]  # un même GIF n'est jamais généré deux fois en même temps
cle_api = APIKeyHeader(name="X-API-Key", auto_error=False)
# Enfant du logger d'uvicorn : les lignes sortent dans les logs du conteneur, au même format
journal = logging.getLogger("uvicorn.error").getChild("acces")


NOM_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
cles = []  # (nom, clé), rempli au démarrage


def lire_cles(valeur):
    """Découpe « alice:cle1,bob:cle2 ». Les messages d'erreur ne citent jamais une clé."""
    resultat = []
    for i, entree in enumerate((e.strip() for e in valeur.split(",") if e.strip()), 1):
        nom, separateur, cle = (morceau.strip() for morceau in entree.partition(":"))
        if not separateur or not NOM_RE.fullmatch(nom):
            raise ValueError(f"entrée n° {i} : format attendu nom:clé (nom en lettres, chiffres, _ . @ -)")
        if len(cle) < 16:
            raise ValueError(f"clé de {nom} trop courte (16 caractères au moins)")
        resultat.append((nom, cle))
    if not resultat:
        raise ValueError("aucune clé")
    if len({nom for nom, _ in resultat}) < len(resultat):
        raise ValueError("un nom apparaît deux fois")
    if len({cle for _, cle in resultat}) < len(resultat):
        raise ValueError("deux personnes ont la même clé")
    return resultat


def proprietaire(cle):
    trouve = None
    for nom, attendue in cles:  # toutes comparées, en temps constant : rien ne fuite par la durée
        if secrets.compare_digest(cle.encode(), attendue.encode()):
            trouve = nom
    return trouve


def verifier_cle(request: Request, cle: Annotated[str | None, Depends(cle_api)]):
    cible = f"{request.method} {request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
    nom = proprietaire(cle) if cle else None
    if nom is None:
        journal.warning("clé %s refusée : %s", "absente" if not cle else "inconnue", cible)
        raise HTTPException(401, "Clé d'API absente ou invalide (en-tête X-API-Key).")
    request.state.proprietaire = nom
    journal.info("%s : %s", nom, cible)


@contextlib.asynccontextmanager
async def demarrage(_app):
    try:
        cles[:] = lire_cles(API_KEYS)
    except ValueError as e:
        raise RuntimeError(f"Variable API_KEYS inutilisable ({e}) : l'API refuse de démarrer sans clé.")
    journal.info("%d clé(s) : %s", len(cles), ", ".join(nom for nom, _ in cles))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for reste in CACHE_DIR.glob("tmp*"):  # générations interrompues par un arrêt brutal
        shutil.rmtree(reste, ignore_errors=True)
    yield


app = FastAPI(title="Kaamelott GIF", lifespan=demarrage, dependencies=[Depends(verifier_cle)])


@contextlib.contextmanager
def ouvrir_db():
    # Une connexion par requête : une base remplacée (mv) est vue dès la requête suivante
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("SELECT 1 FROM episodes LIMIT 1")
    except sqlite3.Error as e:
        raise HTTPException(503, f"Base illisible ({DB_PATH}) : {e}. Si elle est en mode WAL, "
                                 "exporte-la avec VACUUM INTO (voir api/README.md).")
    try:
        yield con
    finally:
        con.close()


def chemin_video(fichier):
    """Retrouve dans MEDIA_DIR la vidéo d'un chemin de la base (relatif à data/ sur le poste)."""
    relatif = Path(fichier)
    if relatif.is_absolute():
        raise HTTPException(500, f"Vidéo hors du dossier data/ du poste, introuvable sur le serveur : {fichier}")
    chemin = chemin_dans(relatif, MEDIA_DIR).resolve()
    if not chemin.is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(500, "Chemin de vidéo hors de MEDIA_DIR.")
    if not chemin.is_file():
        raise HTTPException(500, f"Vidéo introuvable dans MEDIA_DIR : {relatif}")
    return relatif, chemin


def nettoyer_cache():
    """Supprime les GIF les moins récemment servis quand le cache dépasse sa taille maximale."""
    gifs = []
    for f in CACHE_DIR.glob("*.gif"):
        with contextlib.suppress(FileNotFoundError):
            st = f.stat()
            gifs.append((st.st_mtime, st.st_size, f))
    total = sum(taille for _, taille, _ in gifs)
    for _, taille, f in sorted(gifs):
        if total <= CACHE_MAX_OCTETS * 0.9:
            break
        f.unlink(missing_ok=True)
        total -= taille


def generer(fichier_cache, video, debut, duree, texte, largeur, fps):
    if not generations.acquire(timeout=ATTENTE_MAX):
        raise HTTPException(503, "Trop de GIF en cours de génération, réessaie dans un instant.",
                            headers={"Retry-After": "10"})
    try:
        # Rendu dans un dossier temporaire du cache, puis renommage : jamais de GIF incomplet servi
        with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
            calque, sortie = Path(tmp) / "texte.png", Path(tmp) / "rendu.gif"
            dessiner_texte(texte, largeur, str(calque))
            exporter(str(video), debut, duree, str(calque), largeur, fps, str(sortie), delai=DELAI_FFMPEG)
            os.replace(sortie, fichier_cache)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Génération du GIF trop longue.")
    except subprocess.CalledProcessError:
        raise HTTPException(500, "Échec de ffmpeg (voir les logs du conteneur).")
    finally:
        generations.release()
    nettoyer_cache()


@app.get("/repliques")
def chercher_repliques(
    q: Annotated[str, Query(min_length=1, max_length=200, description="Réplique à chercher")],
    limit: Annotated[int, Query(ge=1, le=50)] = 15,
):
    """Cherche la phrase exacte, sinon tous les mots dans n'importe quel ordre."""
    with ouvrir_db() as con:
        resultats = rechercher(con, q, limit)
    return [{
        "id": r["id"],
        "livre": r["livre"],
        "numero": r["numero"],
        "titre": r["titre"],
        "debut": r["debut"],
        "fin": r["fin"],
        "texte": r["texte"],
        "avant": r["avant"],
        "apres": r["apres"],
        "gif": app.url_path_for("gif_replique", cue_id=r["id"]),
    } for r in resultats]


@app.get("/repliques/{cue_id}/gif", name="gif_replique",
         responses={200: {"content": {"image/gif": {}}}, 304: {"description": "GIF inchangé"}})
def gif_replique(
    cue_id: int,
    request: Request,
    texte: Annotated[str | None, Query(max_length=200, description="Remplace le texte transcrit")] = None,
    avant: Annotated[float, Query(ge=0, le=5, description="Secondes gardées avant la réplique")] = 0.2,
    apres: Annotated[float, Query(ge=0, le=5, description="Secondes gardées après la réplique")] = 0.4,
    largeur: Annotated[int, Query(ge=160, le=720)] = 480,
    fps: Annotated[int, Query(ge=5, le=20)] = 12,
):
    """Génère le GIF d'une réplique (id renvoyé par /repliques), ou le ressert depuis le cache."""
    with ouvrir_db() as con:
        r = con.execute(
            """SELECT c.debut, c.fin, c.texte, e.livre, e.numero, e.fichier
               FROM cues c JOIN episodes e ON e.id = c.episode_id WHERE c.id = ?""",
            (cue_id,)).fetchone()
        if r is None:
            raise HTTPException(404, f"Pas de réplique n° {cue_id} (les numéros changent si un épisode "
                                     "est re-transcrit : refais la recherche).")
        relatif, video = chemin_video(r["fichier"])

    texte = (texte or "").strip() or r["texte"]
    avant, apres = round(avant, 1), round(apres, 1)  # 0.2 et 0.20 donnent le même GIF en cache
    debut = round(max(0.0, r["debut"] - avant), 3)
    duree = round(r["fin"] + apres - debut, 3)
    if duree > DUREE_MAX:
        raise HTTPException(422, f"Passage trop long ({duree:.1f} s, maximum {DUREE_MAX:.0f} s).")

    # La clé décrit l'image elle-même : elle reste valable même si les numéros de réplique changent
    description = [VERSION_RENDU, str(relatif), debut, duree, texte, largeur, fps]
    cle = hashlib.sha256(json.dumps(description, ensure_ascii=False).encode()).hexdigest()[:32]
    entetes = {"ETag": f'"{cle}"', "Cache-Control": "private, max-age=86400"}
    if request.headers.get("if-none-match") == entetes["ETag"]:
        return Response(status_code=304, headers=entetes)

    fichier_cache = CACHE_DIR / f"{cle}.gif"
    for _ in range(2):  # deuxième tour si le nettoyage du cache a supprimé le GIF entre-temps
        if not fichier_cache.is_file():
            with verrous[int(cle[:8], 16) % len(verrous)]:
                if not fichier_cache.is_file():
                    generer(fichier_cache, video, debut, duree, texte, largeur, fps)
        try:
            contenu = fichier_cache.read_bytes()  # lu en mémoire : le nettoyage ne peut plus casser la réponse
        except FileNotFoundError:
            continue
        with contextlib.suppress(FileNotFoundError):
            os.utime(fichier_cache)  # marque le GIF comme récemment servi
        break
    else:
        raise HTTPException(503, "Cache saturé, réessaie dans un instant.", headers={"Retry-After": "10"})
    nom = nom_par_defaut({"livre": r["livre"], "numero": r["numero"], "debut": r["debut"]}, texte)
    entetes["Content-Disposition"] = f'inline; filename="{nom}"'
    return Response(contenu, media_type="image/gif", headers=entetes)
