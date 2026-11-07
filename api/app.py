"""REST API: cue search and GIF export.

Reuses the search (cli/db.py) and the GIF rendering (cli/gif.py) of the command-line scripts.
The database is opened read-only: indexing happens elsewhere, with cli/ingest.py.
Routes, query parameters and JSON fields keep their French names: they are the public interface.
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
from db import search, video_path  # noqa: E402
from gif import default_name, draw_caption, export_gif  # noqa: E402

DB_PATH = Path(os.environ.get("DB_PATH", "/data/kaamelott.db"))
# Equivalent of the workstation's data/ folder: the database locates videos as "Kaamelott/S01/…"
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/cache"))
CACHE_MAX_BYTES = int(os.environ.get("CACHE_MAX_MO", "2000")) * 1_000_000
MAX_FFMPEG = int(os.environ.get("MAX_FFMPEG", "2"))
# One key per person: "alice:key1,bob:key2". The name appears in the log of each request.
API_KEYS = os.environ.get("API_KEYS", "")

RENDER_VERSION = 1    # bump when the rendering changes: older cached GIFs are then ignored
MAX_DURATION = 15.0   # seconds of video per GIF
MAX_WAIT = 30         # seconds to wait for a free ffmpeg slot
FFMPEG_TIMEOUT = 120

ffmpeg_slots = threading.BoundedSemaphore(MAX_FFMPEG)
locks = [threading.Lock() for _ in range(64)]  # the same GIF is never rendered twice at the same time
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
# Child of uvicorn's logger: lines go to the container logs, in the same format
log = logging.getLogger("uvicorn.error").getChild("acces")


NAME_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
keys = []  # (name, key), filled at startup


def parse_keys(value):
    """Splits "alice:key1,bob:key2". Error messages never quote a key."""
    result = []
    for i, entry in enumerate((e.strip() for e in value.split(",") if e.strip()), 1):
        name, separator, key = (part.strip() for part in entry.partition(":"))
        if not separator or not NAME_RE.fullmatch(name):
            raise ValueError(f"entrée n° {i} : format attendu nom:clé (nom en lettres, chiffres, _ . @ -)")
        if len(key) < 16:
            raise ValueError(f"clé de {name} trop courte (16 caractères au moins)")
        result.append((name, key))
    if not result:
        raise ValueError("aucune clé")
    if len({name for name, _ in result}) < len(result):
        raise ValueError("un nom apparaît deux fois")
    if len({key for _, key in result}) < len(result):
        raise ValueError("deux personnes ont la même clé")
    return result


def key_owner(key):
    owner = None
    for name, expected in keys:  # all compared, in constant time: nothing leaks through timing
        if secrets.compare_digest(key.encode(), expected.encode()):
            owner = name
    return owner


def check_key(request: Request, key: Annotated[str | None, Depends(api_key_header)]):
    target = f"{request.method} {request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
    name = key_owner(key) if key else None
    if name is None:
        log.warning("clé %s refusée : %s", "absente" if not key else "inconnue", target)
        raise HTTPException(401, "Clé d'API absente ou invalide (en-tête X-API-Key).")
    request.state.owner = name
    log.info("%s : %s", name, target)


@contextlib.asynccontextmanager
async def lifespan(_app):
    try:
        keys[:] = parse_keys(API_KEYS)
    except ValueError as e:
        raise RuntimeError(f"Variable API_KEYS inutilisable ({e}) : l'API refuse de démarrer sans clé.")
    log.info("%d clé(s) : %s", len(keys), ", ".join(name for name, _ in keys))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for leftover in CACHE_DIR.glob("tmp*"):  # renders interrupted by a hard stop
        shutil.rmtree(leftover, ignore_errors=True)
    yield


app = FastAPI(title="Kaamelott GIF", lifespan=lifespan, dependencies=[Depends(check_key)])


@contextlib.contextmanager
def open_db():
    # One connection per request: a replaced database (mv) is seen from the next request on
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


def resolve_video(stored):
    """Finds in MEDIA_DIR the video of a database path (relative to data/ on the workstation)."""
    relative = Path(stored)
    if relative.is_absolute():
        raise HTTPException(500, f"Vidéo hors du dossier data/ du poste, introuvable sur le serveur : {stored}")
    path = video_path(relative, MEDIA_DIR).resolve()
    if not path.is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(500, "Chemin de vidéo hors de MEDIA_DIR.")
    if not path.is_file():
        raise HTTPException(500, f"Vidéo introuvable dans MEDIA_DIR : {relative}")
    return relative, path


def prune_cache():
    """Deletes the least recently served GIFs when the cache exceeds its maximum size."""
    gifs = []
    for f in CACHE_DIR.glob("*.gif"):
        with contextlib.suppress(FileNotFoundError):
            st = f.stat()
            gifs.append((st.st_mtime, st.st_size, f))
    total = sum(size for _, size, _ in gifs)
    for _, size, f in sorted(gifs):
        if total <= CACHE_MAX_BYTES * 0.9:
            break
        f.unlink(missing_ok=True)
        total -= size


def render(cache_file, video, start, duration, text, width, fps):
    if not ffmpeg_slots.acquire(timeout=MAX_WAIT):
        raise HTTPException(503, "Trop de GIF en cours de génération, réessaie dans un instant.",
                            headers={"Retry-After": "10"})
    try:
        # Rendered in a temporary folder of the cache, then renamed: a partial GIF is never served
        with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
            overlay, output = Path(tmp) / "caption.png", Path(tmp) / "render.gif"
            draw_caption(text, width, str(overlay))
            export_gif(str(video), start, duration, str(overlay), width, fps, str(output), timeout=FFMPEG_TIMEOUT)
            os.replace(output, cache_file)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Génération du GIF trop longue.")
    except subprocess.CalledProcessError:
        raise HTTPException(500, "Échec de ffmpeg (voir les logs du conteneur).")
    finally:
        ffmpeg_slots.release()
    prune_cache()


@app.get("/repliques")
def search_quotes(
    q: Annotated[str, Query(min_length=1, max_length=200, description="Réplique à chercher")],
    limit: Annotated[int, Query(ge=1, le=50)] = 15,
):
    """Cherche la phrase exacte, sinon tous les mots dans n'importe quel ordre."""
    with open_db() as con:
        results = search(con, q, limit)
    return [{
        "id": r["id"],
        "livre": r["livre"],
        "numero": r["numero"],
        "titre": r["titre"],
        "debut": r["debut"],
        "fin": r["fin"],
        "texte": r["texte"],
        "avant": r["before"],
        "apres": r["after"],
        "gif": app.url_path_for("quote_gif", cue_id=r["id"]),
    } for r in results]


@app.get("/repliques/{cue_id}/gif", name="quote_gif",
         responses={200: {"content": {"image/gif": {}}}, 304: {"description": "GIF inchangé"}})
def quote_gif(
    cue_id: int,
    request: Request,
    text: Annotated[str | None, Query(alias="texte", max_length=200,
                                      description="Remplace le texte transcrit")] = None,
    before: Annotated[float, Query(alias="avant", ge=0, le=5,
                                   description="Secondes gardées avant la réplique")] = 0.2,
    after: Annotated[float, Query(alias="apres", ge=0, le=5,
                                  description="Secondes gardées après la réplique")] = 0.4,
    width: Annotated[int, Query(alias="largeur", ge=160, le=720)] = 480,
    fps: Annotated[int, Query(ge=5, le=20)] = 12,
):
    """Génère le GIF d'une réplique (id renvoyé par /repliques), ou le ressert depuis le cache."""
    with open_db() as con:
        r = con.execute(
            """SELECT c.debut, c.fin, c.texte, e.livre, e.numero, e.fichier
               FROM cues c JOIN episodes e ON e.id = c.episode_id WHERE c.id = ?""",
            (cue_id,)).fetchone()
        if r is None:
            raise HTTPException(404, f"Pas de réplique n° {cue_id} (les numéros changent si un épisode "
                                     "est re-transcrit : refais la recherche).")
        relative, video = resolve_video(r["fichier"])

    text = (text or "").strip() or r["texte"]
    before, after = round(before, 1), round(after, 1)  # 0.2 and 0.20 give the same cached GIF
    start = round(max(0.0, r["debut"] - before), 3)
    duration = round(r["fin"] + after - start, 3)
    if duration > MAX_DURATION:
        raise HTTPException(422, f"Passage trop long ({duration:.1f} s, maximum {MAX_DURATION:.0f} s).")

    # The key describes the image itself: it stays valid even if cue ids change
    description = [RENDER_VERSION, str(relative), start, duration, text, width, fps]
    key = hashlib.sha256(json.dumps(description, ensure_ascii=False).encode()).hexdigest()[:32]
    headers = {"ETag": f'"{key}"', "Cache-Control": "private, max-age=86400"}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)

    cache_file = CACHE_DIR / f"{key}.gif"
    for _ in range(2):  # second round if pruning deleted the GIF in the meantime
        if not cache_file.is_file():
            with locks[int(key[:8], 16) % len(locks)]:
                if not cache_file.is_file():
                    render(cache_file, video, start, duration, text, width, fps)
        try:
            content = cache_file.read_bytes()  # read into memory: pruning can no longer break the response
        except FileNotFoundError:
            continue
        with contextlib.suppress(FileNotFoundError):
            os.utime(cache_file)  # marks the GIF as recently served
        break
    else:
        raise HTTPException(503, "Cache saturé, réessaie dans un instant.", headers={"Retry-After": "10"})
    name = default_name({"livre": r["livre"], "numero": r["numero"], "debut": r["debut"]}, text)
    headers["Content-Disposition"] = f'inline; filename="{name}"'
    return Response(content, media_type="image/gif", headers=headers)
