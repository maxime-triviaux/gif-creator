#!/usr/bin/env python3
"""Recherche une réplique dans la base.

Usage :
  python search.py "c'est pas faux"        # liste les résultats, numérotés
  python search.py "c'est pas faux" -o 2   # ouvre le résultat n° 2 au bon moment
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from db import connect, rechercher

ROMAINS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}
GRIS, GRAS, FIN = "\033[90m", "\033[1m", "\033[0m"
VLC = "/Applications/VLC.app/Contents/MacOS/VLC"
MARGE = 1.0  # secondes de lecture avant la réplique (les temps de Whisper sont approximatifs)


def tc(s):
    m, s = divmod(s, 60)
    return f"{int(m):02d}:{s:04.1f}"


def commande_lecteur(fichier, debut):
    """Renvoie la commande qui lit `fichier` à partir de `debut` secondes, ou None sans lecteur."""
    if Path(VLC).exists():
        return [VLC, f"--start-time={debut}", fichier]
    if shutil.which("ffplay"):
        return ["ffplay", "-loglevel", "error", "-ss", str(debut), fichier]
    return None


def ouvrir(r):
    if not Path(r["fichier"]).is_file():
        sys.exit(f"Fichier introuvable : {r['fichier']}")
    debut = max(0.0, r["debut"] - MARGE)
    commande = commande_lecteur(r["fichier"], debut)
    if commande is None:
        sys.exit("Aucun lecteur trouvé : installe VLC ou ffmpeg (ffplay).")
    print(f"Ouverture à {tc(debut)} : {r['fichier']}")
    # Lecteur détaché : le terminal reste disponible et peut être fermé
    subprocess.Popen(commande, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("requete")
    ap.add_argument("--db", default="kaamelott.db")
    ap.add_argument("-n", type=int, default=15)
    ap.add_argument("-o", "--ouvrir", type=int, metavar="N", help="Ouvre le résultat n° N dans le lecteur vidéo")
    args = ap.parse_args()

    res = rechercher(connect(args.db), args.requete, max(args.n, args.ouvrir or 0))
    if not res:
        print("Aucun résultat.")
        return
    for i, r in enumerate(res, 1):
        titre = f" · {r['titre']}" if r["titre"] else ""
        print(f"{GRAS}[{i}] Livre {ROMAINS.get(r['livre'], r['livre'])} – ép. {r['numero']}{titre}{FIN}"
              f"  {tc(r['debut'])} → {tc(r['fin'])}")
        for t in r["avant"]:
            print(f"   {GRIS}{t}{FIN}")
        print(f" ▶ {r['texte']}")
        for t in r["apres"]:
            print(f"   {GRIS}{t}{FIN}")
        print()

    if args.ouvrir is not None:
        if not 1 <= args.ouvrir <= len(res):
            sys.exit(f"Pas de résultat n° {args.ouvrir} : {len(res)} résultat(s) seulement.")
        ouvrir(res[args.ouvrir - 1])


if __name__ == "__main__":
    main()
