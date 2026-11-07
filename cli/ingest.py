#!/usr/bin/env python3
"""Transcrit les épisodes de Kaamelott avec Whisper et remplit la base SQLite.

Usage :
  python cli/ingest.py data/Kaamelott                         # tout (reprend où ça s'est arrêté)
  python cli/ingest.py data/Kaamelott --livre 1 --limite 3    # test rapide
  python cli/ingest.py data/Kaamelott --refaire               # re-transcrit même ce qui est déjà fait

Le dossier doit contenir des sous-dossiers S01, S02, ... avec les .mkv dedans.
"""
import argparse
import datetime
import platform
import re
import sys
import time
from pathlib import Path

from db import DEFAULT_DB, connect, stored_path

# No initial_prompt: with a list of proper names, Whisper dropped whole lines
# (about 20 s on "heat") and made some up.
# No hallucination_silence_threshold either: at 2 s, it removed real lines.

EPISODE_RE = re.compile(r"S(\d{1,2})\s*[E.x]\s*(\d{1,3})", re.I)


def natural_key(path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def parse_name(path, folder_book, rank):
    """Book, number and title of an episode, from `SxxExx` in the file name, otherwise from the folder and rank."""
    m = EPISODE_RE.search(path.stem)
    if m:
        book, number, rest = int(m[1]), int(m[2]), path.stem[m.end():]
    else:
        book, number, rest = folder_book, rank, path.stem
    title = re.sub(r"^[\s\-_.]+", "", rest).replace("_", " ").strip() or None
    # stored: the form saved in the database (relative to data/); path: the video to transcribe
    return {"book": book, "number": number, "title": title,
            "stored": stored_path(path), "path": str(path.resolve())}


def list_episodes(root, books=None):
    episodes = []
    folders = sorted((d for d in root.iterdir()
                      if d.is_dir() and re.fullmatch(r"S\d+", d.name, re.I)), key=natural_key)
    for folder in folders:
        book = int(folder.name[1:])
        if books and book not in books:
            continue
        for rank, f in enumerate(sorted(folder.glob("*.mkv"), key=natural_key), 1):
            episodes.append(parse_name(f, book, rank))
    return episodes


def make_transcriber(backend, model):
    """Returns (function(path) -> list of segments, engine name)."""
    if backend == "auto":
        apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
        backend = "mlx" if apple_silicon else "faster"

    if backend == "mlx":
        import mlx_whisper
        repo = model or "mlx-community/whisper-large-v3-turbo"

        def run(path):
            r = mlx_whisper.transcribe(
                path, path_or_hf_repo=repo, language="fr", word_timestamps=True,
                condition_on_previous_text=False)
            return [(s["start"], s["end"], s["text"].strip(),
                     [(w["start"], w["end"], w["word"].strip()) for w in s.get("words", [])])
                    for s in r["segments"]]
        return run, f"mlx:{repo}"

    from faster_whisper import WhisperModel
    name = model or "large-v3-turbo"
    whisper = WhisperModel(name, device="cpu", compute_type="int8")

    def run(path):
        segments, _ = whisper.transcribe(
            path, language="fr", word_timestamps=True, vad_filter=True,
            condition_on_previous_text=False)
        return [(s.start, s.end, s.text.strip(),
                 [(w.start, w.end, w.word.strip()) for w in (s.words or [])])
                for s in segments]
    return run, f"faster-whisper:{name}"


def save(con, episode, segments, engine):
    with con:  # one transaction per episode: a Ctrl-C never leaves one half written
        con.execute(
            """INSERT INTO episodes(livre, numero, titre, fichier) VALUES (?,?,?,?)
               ON CONFLICT(fichier) DO UPDATE SET
                 livre=excluded.livre, numero=excluded.numero, titre=excluded.titre""",
            (episode["book"], episode["number"], episode["title"], episode["stored"]))
        episode_id = con.execute("SELECT id FROM episodes WHERE fichier=?", (episode["stored"],)).fetchone()[0]
        con.execute("DELETE FROM cues WHERE episode_id=?", (episode_id,))
        for start, end, text, words in segments:
            if not text:
                continue
            cue_id = con.execute(
                "INSERT INTO cues(episode_id, debut, fin, texte) VALUES (?,?,?,?)",
                (episode_id, start, end, text)).lastrowid
            con.executemany(
                "INSERT INTO mots(cue_id, idx, debut, fin, mot) VALUES (?,?,?,?,?)",
                [(cue_id, i, s, e, w) for i, (s, e, w) in enumerate(words)])
        duration = segments[-1][1] if segments else None
        con.execute(
            "UPDATE episodes SET duree=?, moteur=?, transcrit_le=? WHERE id=?",
            (duration, engine, datetime.datetime.now().isoformat(timespec="seconds"), episode_id))
    return len(segments)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", metavar="dossier", help="Dossier contenant S01, S02, ...")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--backend", choices=["auto", "mlx", "faster"], default="auto")
    ap.add_argument("--modele", metavar="MODELE", dest="model", help="Modèle Whisper (défaut : large-v3-turbo)")
    ap.add_argument("--livre", metavar="LIVRE", dest="books", type=int, action="append", help="Limiter à un Livre (répétable)")
    ap.add_argument("--limite", metavar="LIMITE", dest="limit", type=int, help="Nombre max d'épisodes à traiter")
    ap.add_argument("--refaire", dest="redo", action="store_true", help="Re-transcrire les épisodes déjà faits")
    args = ap.parse_args()

    root = Path(args.folder).expanduser()
    if not root.is_dir():
        sys.exit(f"Dossier introuvable : {root}")

    con = connect(args.db)
    episodes = list_episodes(root, args.books)
    if not episodes:
        sys.exit("Aucun .mkv trouvé dans des sous-dossiers S01, S02, ...")

    done = {r[0] for r in con.execute("SELECT fichier FROM episodes WHERE transcrit_le IS NOT NULL")}
    todo = [e for e in episodes if args.redo or e["stored"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    done_count = sum(e["stored"] in done for e in episodes)
    print(f"{len(episodes)} épisodes trouvés, {done_count} déjà transcrits, {len(todo)} à traiter.")
    if not todo:
        return

    print("Chargement du modèle Whisper (le 1er lancement le télécharge, ~1,5 Go)…")
    transcribe, engine = make_transcriber(args.backend, args.model)
    print(f"Moteur : {engine}\n")

    t0 = time.time()
    for i, episode in enumerate(todo, 1):
        name = f"Livre {episode['book']} – ép. {episode['number']}" + (f" · {episode['title']}" if episode["title"] else "")
        print(f"[{i}/{len(todo)}] {name} … ", end="", flush=True)
        t = time.time()
        try:
            n = save(con, episode, transcribe(episode["path"]), engine)
        except KeyboardInterrupt:
            print("\nInterrompu. Relance la même commande pour reprendre.")
            return
        except Exception as e:  # a corrupted file does not stop the others
            print(f"ERREUR : {e}")
            continue
        elapsed = time.time() - t0
        remaining = elapsed / i * (len(todo) - i)
        print(f"{n} répliques en {time.time() - t:.0f}s  (reste ~{remaining / 60:.0f} min)")

    total = con.execute("SELECT COUNT(*) FROM cues").fetchone()[0]
    print(f"\nTerminé. {total} répliques dans {args.db}.")


if __name__ == "__main__":
    main()
