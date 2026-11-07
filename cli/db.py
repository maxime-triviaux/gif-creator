"""Base SQLite de la bibliothèque Kaamelott : épisodes, répliques, index plein texte."""
import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes(
  id            INTEGER PRIMARY KEY,
  livre         INTEGER NOT NULL,
  numero        INTEGER NOT NULL,
  titre         TEXT,
  fichier       TEXT NOT NULL UNIQUE,
  duree         REAL,
  moteur        TEXT,
  transcrit_le  TEXT
);
CREATE INDEX IF NOT EXISTS episodes_ordre ON episodes(livre, numero);

-- Une "cue" = un segment de dialogue horodaté (≈ une phrase)
CREATE TABLE IF NOT EXISTS cues(
  id          INTEGER PRIMARY KEY,
  episode_id  INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  debut       REAL NOT NULL,
  fin         REAL NOT NULL,
  texte       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cues_ep ON cues(episode_id, debut);

-- Horodatage au mot près (pour caler les GIF finement)
CREATE TABLE IF NOT EXISTS mots(
  cue_id  INTEGER NOT NULL REFERENCES cues(id) ON DELETE CASCADE,
  idx     INTEGER NOT NULL,
  debut   REAL,
  fin     REAL,
  mot     TEXT,
  PRIMARY KEY(cue_id, idx)
);

-- Index plein texte : insensible à la casse, aux accents et aux apostrophes
-- ("cest pas faux" trouve "C'est pas faux !")
CREATE VIRTUAL TABLE IF NOT EXISTS cues_fts USING fts5(
  norm, tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS cues_ai AFTER INSERT ON cues BEGIN
  INSERT INTO cues_fts(rowid, norm)
  VALUES (new.id, replace(replace(new.texte, char(39), ''), '’', ''));
END;
CREATE TRIGGER IF NOT EXISTS cues_ad AFTER DELETE ON cues BEGIN
  DELETE FROM cues_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS cues_au AFTER UPDATE OF texte ON cues BEGIN
  UPDATE cues_fts SET norm = replace(replace(new.texte, char(39), ''), '’', '')
  WHERE rowid = new.id;
END;
"""


# Fixé par rapport au projet, pas au dossier courant : les scripts se lancent de n'importe où
DOSSIER_DATA = Path(__file__).resolve().parent.parent / "data"
BASE_DEFAUT = DOSSIER_DATA / "kaamelott.db"


def chemin_stocke(video):
    """Chemin enregistré en base : relatif à data/ (« Kaamelott/S01/… ») quand la vidéo y est,
    pour que la base reste valable si le projet ou les vidéos changent de machine ; absolu sinon."""
    chemin = Path(video).resolve()
    try:
        return chemin.relative_to(DOSSIER_DATA).as_posix()
    except ValueError:
        return str(chemin)


def chemin_video(fichier, racine=DOSSIER_DATA):
    """Chemin réel d'une vidéo de la base. Un chemin absolu reste tel quel."""
    return racine / fichier


def connect(chemin=BASE_DEFAUT):
    chemin = Path(chemin).expanduser()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(chemin)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    return con


def _tokens(q):
    return re.findall(r"\w+", q.lower().replace("'", "").replace("’", ""))


def rechercher(con, requete, limite=20, contexte=1):
    """Cherche une réplique. Essaie d'abord la phrase exacte, puis tous les mots
    dans n'importe quel ordre (le dernier en préfixe). Renvoie des dicts."""
    toks = _tokens(requete)
    if not toks:
        return []
    essais = [
        '"' + " ".join(toks) + '"',                       # phrase exacte
        " ".join(f'"{t}"' for t in toks[:-1]) + f' "{toks[-1]}"*',  # tous les mots
    ]
    lignes = []
    for fts in essais:
        lignes = con.execute(
            """SELECT c.id, c.episode_id, c.debut, c.fin, c.texte,
                      e.livre, e.numero, e.titre, e.fichier
               FROM cues_fts f
               JOIN cues c ON c.id = f.rowid
               JOIN episodes e ON e.id = c.episode_id
               WHERE cues_fts MATCH ?
               ORDER BY bm25(cues_fts), e.livre, e.numero, c.debut
               LIMIT ?""",
            (fts, limite),
        ).fetchall()
        if lignes:
            break

    resultats = []
    for r in lignes:
        avant = con.execute(
            "SELECT texte FROM cues WHERE episode_id=? AND debut<? ORDER BY debut DESC LIMIT ?",
            (r["episode_id"], r["debut"], contexte)).fetchall()
        apres = con.execute(
            "SELECT texte FROM cues WHERE episode_id=? AND debut>? ORDER BY debut LIMIT ?",
            (r["episode_id"], r["debut"], contexte)).fetchall()
        d = dict(r)
        d["avant"] = [a["texte"] for a in reversed(avant)]
        d["apres"] = [a["texte"] for a in apres]
        resultats.append(d)
    return resultats
