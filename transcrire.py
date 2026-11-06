#!/usr/bin/env python3
"""Transcrit les épisodes (.mkv) du dossier courant et les ajoute à la base SQLite.

Contrairement à ingest.py, pas besoin de sous-dossiers S01, S02, ...

Usage :
  python transcrire.py                            # tous les .mkv du dossier courant
  python transcrire.py heat.mkv --livre 1 --numero 5
  python transcrire.py --refaire                  # re-transcrit même ce qui est déjà fait

Un nom de fichier « SxxExx - Titre.mkv » fixe le Livre et le numéro ; sinon ce sont
--livre et --numero qui s'appliquent, et le nom du fichier sert de titre.
"""
import argparse
import sys
from pathlib import Path

from db import connect
from ingest import analyser_nom, creer_transcripteur, enregistrer, tri_naturel
from search import tc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fichiers", nargs="*", type=Path, help="Fichiers à transcrire (défaut : *.mkv du dossier courant)")
    ap.add_argument("--db", default="kaamelott.db")
    ap.add_argument("--backend", choices=["auto", "mlx", "faster"], default="auto")
    ap.add_argument("--modele", help="Modèle Whisper (défaut : large-v3-turbo)")
    ap.add_argument("--livre", type=int, default=0, help="Livre si absent du nom de fichier (défaut : 0)")
    ap.add_argument("--numero", type=int, default=1, help="Numéro du 1er épisode si absent du nom de fichier (défaut : 1)")
    ap.add_argument("--refaire", action="store_true", help="Re-transcrire les épisodes déjà faits")
    args = ap.parse_args()

    fichiers = args.fichiers or sorted(Path.cwd().glob("*.mkv"), key=tri_naturel)
    if not fichiers:
        sys.exit("Aucun .mkv trouvé dans le dossier courant.")
    for f in fichiers:
        if not f.is_file():
            sys.exit(f"Fichier introuvable : {f}")

    con = connect(args.db)
    deja = {r[0] for r in con.execute("SELECT fichier FROM episodes WHERE transcrit_le IS NOT NULL")}
    eps = [analyser_nom(f, args.livre, args.numero + i) for i, f in enumerate(fichiers)]
    a_faire = [e for e in eps if args.refaire or e["fichier"] not in deja]
    print(f"{len(eps)} épisodes trouvés, {len(eps) - len(a_faire)} déjà transcrits, {len(a_faire)} à traiter.")
    if not a_faire:
        return

    print("Chargement du modèle Whisper (le 1er lancement le télécharge, ~1,5 Go)…")
    transcrire, moteur = creer_transcripteur(args.backend, args.modele)
    print(f"Moteur : {moteur}\n")

    for ep in a_faire:
        print(f"Livre {ep['livre']} – ép. {ep['numero']} · {ep['titre']}")
        try:
            segments = transcrire(ep["fichier"])
            n = enregistrer(con, ep, segments, moteur)
        except KeyboardInterrupt:
            print("\nInterrompu. Relance la même commande pour reprendre.")
            return
        except Exception as e:  # un fichier corrompu ne bloque pas le reste
            print(f"ERREUR : {e}")
            continue
        for debut, fin, texte, _ in segments:
            print(f"  {tc(debut)} → {tc(fin)}  {texte}")
        print(f"{n} répliques enregistrées dans {args.db}.\n")


if __name__ == "__main__":
    main()
