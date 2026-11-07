#!/usr/bin/env python3
"""Transcrit les épisodes (.mkv) du dossier courant et les ajoute à la base SQLite.

Contrairement à ingest.py, pas besoin de sous-dossiers S01, S02, ...

Usage :
  python cli/transcribe.py                            # tous les .mkv du dossier courant
  python cli/transcribe.py heat.mkv --livre 1 --numero 5
  python cli/transcribe.py --refaire                  # re-transcrit même ce qui est déjà fait

Un nom de fichier « SxxExx - Titre.mkv » fixe le Livre et le numéro ; sinon ce sont
--livre et --numero qui s'appliquent, et le nom du fichier sert de titre.
"""
import argparse
import sys
from pathlib import Path

from db import DEFAULT_DB, connect
from ingest import make_transcriber, natural_key, parse_name, save
from search import timecode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", metavar="fichiers", nargs="*", type=Path,
                    help="Fichiers à transcrire (défaut : *.mkv du dossier courant)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--backend", choices=["auto", "mlx", "faster"], default="auto")
    ap.add_argument("--modele", metavar="MODELE", dest="model", help="Modèle Whisper (défaut : large-v3-turbo)")
    ap.add_argument("--livre", metavar="LIVRE", dest="book", type=int, default=0, help="Livre si absent du nom de fichier (défaut : 0)")
    ap.add_argument("--numero", metavar="NUMERO", dest="number", type=int, default=1,
                    help="Numéro du 1er épisode si absent du nom de fichier (défaut : 1)")
    ap.add_argument("--refaire", dest="redo", action="store_true", help="Re-transcrire les épisodes déjà faits")
    args = ap.parse_args()

    files = args.files or sorted(Path.cwd().glob("*.mkv"), key=natural_key)
    if not files:
        sys.exit("Aucun .mkv trouvé dans le dossier courant.")
    for f in files:
        if not f.is_file():
            sys.exit(f"Fichier introuvable : {f}")

    con = connect(args.db)
    done = {r[0] for r in con.execute("SELECT fichier FROM episodes WHERE transcrit_le IS NOT NULL")}
    episodes = [parse_name(f, args.book, args.number + i) for i, f in enumerate(files)]
    todo = [e for e in episodes if args.redo or e["stored"] not in done]
    print(f"{len(episodes)} épisodes trouvés, {len(episodes) - len(todo)} déjà transcrits, {len(todo)} à traiter.")
    if not todo:
        return

    print("Chargement du modèle Whisper (le 1er lancement le télécharge, ~1,5 Go)…")
    transcribe, engine = make_transcriber(args.backend, args.model)
    print(f"Moteur : {engine}\n")

    for episode in todo:
        print(f"Livre {episode['book']} – ép. {episode['number']} · {episode['title']}")
        try:
            segments = transcribe(episode["path"])
            n = save(con, episode, segments, engine)
        except KeyboardInterrupt:
            print("\nInterrompu. Relance la même commande pour reprendre.")
            return
        except Exception as e:  # a corrupted file does not stop the others
            print(f"ERREUR : {e}")
            continue
        for start, end, text, _ in segments:
            print(f"  {timecode(start)} → {timecode(end)}  {text}")
        print(f"{n} répliques enregistrées dans {args.db}.\n")


if __name__ == "__main__":
    main()
