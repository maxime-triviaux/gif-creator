#!/usr/bin/env python3
"""Recherche une réplique dans la base.

Usage :
  python cli/search.py "c'est pas faux"        # liste les résultats, numérotés
  python cli/search.py "c'est pas faux" -o 2   # ouvre le résultat n° 2 au bon moment
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from db import DEFAULT_DB, connect, search, video_path

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}
GREY, BOLD, RESET = "\033[90m", "\033[1m", "\033[0m"
VLC = "/Applications/VLC.app/Contents/MacOS/VLC"
LEAD_IN = 1.0  # seconds played before the cue (Whisper timestamps are approximate)


def timecode(seconds):
    minutes, seconds = divmod(round(seconds, 1), 60)  # rounded first: 59.96 s gives 01:00.0, not 00:60.0
    return f"{int(minutes):02d}:{seconds:04.1f}"


def player_command(path, start):
    """Command that plays `path` from `start` seconds, or None when no player is installed."""
    if Path(VLC).exists():
        return [VLC, f"--start-time={start}", path]
    if shutil.which("ffplay"):
        return ["ffplay", "-loglevel", "error", "-ss", str(start), path]
    return None


def open_result(result):
    video = video_path(result["fichier"])
    if not video.is_file():
        sys.exit(f"Fichier introuvable : {video}")
    start = max(0.0, result["debut"] - LEAD_IN)
    command = player_command(str(video), start)
    if command is None:
        sys.exit("Aucun lecteur trouvé : installe VLC ou ffmpeg (ffplay).")
    print(f"Ouverture à {timecode(start)} : {video}")
    # Detached player: the terminal stays usable and can be closed
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", metavar="requete")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("-n", metavar="N", dest="limit", type=int, default=15)
    ap.add_argument("-o", "--ouvrir", dest="open", type=int, metavar="N",
                    help="Ouvre le résultat n° N dans le lecteur vidéo")
    args = ap.parse_args()

    results = search(connect(args.db), args.query, max(args.limit, args.open or 0))
    if not results:
        print("Aucun résultat.")
        return
    for i, r in enumerate(results, 1):
        title = f" · {r['titre']}" if r["titre"] else ""
        print(f"{BOLD}[{i}] Livre {ROMAN.get(r['livre'], r['livre'])} – ép. {r['numero']}{title}{RESET}"
              f"  {timecode(r['debut'])} → {timecode(r['fin'])}")
        for text in r["before"]:
            print(f"   {GREY}{text}{RESET}")
        print(f" ▶ {r['texte']}")
        for text in r["after"]:
            print(f"   {GREY}{text}{RESET}")
        print()

    if args.open is not None:
        if not 1 <= args.open <= len(results):
            sys.exit(f"Pas de résultat n° {args.open} : {len(results)} résultat(s) seulement.")
        open_result(results[args.open - 1])


if __name__ == "__main__":
    main()
