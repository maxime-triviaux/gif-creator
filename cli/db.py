"""SQLite database of the Kaamelott library: episodes, cues (lines of dialogue), full-text index."""
import re
import sqlite3
from pathlib import Path

# Table and column names are part of the stored data (local base and the server's copy): keep them.
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

-- A "cue" is a timestamped segment of dialogue (roughly one sentence)
CREATE TABLE IF NOT EXISTS cues(
  id          INTEGER PRIMARY KEY,
  episode_id  INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  debut       REAL NOT NULL,
  fin         REAL NOT NULL,
  texte       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cues_ep ON cues(episode_id, debut);

-- Word-level timestamps (to cut GIFs precisely)
CREATE TABLE IF NOT EXISTS mots(
  cue_id  INTEGER NOT NULL REFERENCES cues(id) ON DELETE CASCADE,
  idx     INTEGER NOT NULL,
  debut   REAL,
  fin     REAL,
  mot     TEXT,
  PRIMARY KEY(cue_id, idx)
);

-- Full-text index, insensitive to case, accents and apostrophes
-- ("cest pas faux" finds "C'est pas faux !")
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


# Anchored to the project, not the current directory: the scripts can be run from anywhere
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = DATA_DIR / "kaamelott.db"


def stored_path(video):
    """Path saved in the database: relative to data/ ("Kaamelott/S01/…") when the video is there,
    so the database stays valid if the project or the videos move to another machine; absolute otherwise."""
    path = Path(video).resolve()
    try:
        return path.relative_to(DATA_DIR).as_posix()
    except ValueError:
        return str(path)


def video_path(stored, root=DATA_DIR):
    """Actual path of a video from the database. An absolute path is returned unchanged."""
    return root / stored


def connect(path=DEFAULT_DB):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    return con


def _tokens(query):
    return re.findall(r"\w+", query.lower().replace("'", "").replace("’", ""))


def search(con, query, limit=20, context=1):
    """Search for a cue. Tries the exact phrase first, then all the words in any order
    (the last one as a prefix). Returns dicts: the selected columns plus `before` and `after`,
    the texts of the surrounding cues."""
    tokens = _tokens(query)
    if not tokens:
        return []
    attempts = [
        '"' + " ".join(tokens) + '"',                                      # exact phrase
        " ".join(f'"{t}"' for t in tokens[:-1]) + f' "{tokens[-1]}"*',     # all the words
    ]
    rows = []
    for fts in attempts:
        rows = con.execute(
            """SELECT c.id, c.episode_id, c.debut, c.fin, c.texte,
                      e.livre, e.numero, e.titre, e.fichier
               FROM cues_fts f
               JOIN cues c ON c.id = f.rowid
               JOIN episodes e ON e.id = c.episode_id
               WHERE cues_fts MATCH ?
               ORDER BY bm25(cues_fts), e.livre, e.numero, c.debut
               LIMIT ?""",
            (fts, limit),
        ).fetchall()
        if rows:
            break

    results = []
    for row in rows:
        before = con.execute(
            "SELECT texte FROM cues WHERE episode_id=? AND debut<? ORDER BY debut DESC LIMIT ?",
            (row["episode_id"], row["debut"], context)).fetchall()
        after = con.execute(
            "SELECT texte FROM cues WHERE episode_id=? AND debut>? ORDER BY debut LIMIT ?",
            (row["episode_id"], row["debut"], context)).fetchall()
        result = dict(row)
        result["before"] = [b["texte"] for b in reversed(before)]
        result["after"] = [a["texte"] for a in after]
        results.append(result)
    return results
