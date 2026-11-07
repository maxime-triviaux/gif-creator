#!/usr/bin/env python3
"""Transcrit les épisodes de Kaamelott avec Whisper et remplit la base SQLite.

Usage :
  python ingest.py ~/Videos/Kaamelott                  # tout (reprend où ça s'est arrêté)
  python ingest.py ~/Videos/Kaamelott --livre 1 --limite 3   # test rapide
  python ingest.py ~/Videos/Kaamelott --refaire        # re-transcrit même ce qui est déjà fait

Le dossier doit contenir des sous-dossiers S01, S02, ... avec les .mkv dedans.
"""
import argparse
import datetime
import platform
import re
import sys
import time
from pathlib import Path

from db import BASE_DEFAUT, chemin_stocke, connect

# Pas d'initial_prompt : avec une liste de noms propres, Whisper perdait des répliques
# entières (≈ 20 s sur « heat ») et inventait des lignes.
# Pas non plus de hallucination_silence_threshold : à 2 s, il supprimait de vraies répliques.

EP_RE = re.compile(r"S(\d{1,2})\s*[E.x]\s*(\d{1,3})", re.I)


def tri_naturel(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def analyser_nom(f, livre_dossier, rang):
    m = EP_RE.search(f.stem)
    if m:
        livre, numero, reste = int(m[1]), int(m[2]), f.stem[m.end():]
    else:
        livre, numero, reste = livre_dossier, rang, f.stem
    titre = re.sub(r"^[\s\-_.]+", "", reste).replace("_", " ").strip() or None
    # fichier : forme enregistrée en base (relative à data/) ; chemin : la vidéo à transcrire
    return {"livre": livre, "numero": numero, "titre": titre,
            "fichier": chemin_stocke(f), "chemin": str(f.resolve())}


def lister_episodes(racine, livres=None):
    eps = []
    dossiers = sorted((d for d in racine.iterdir()
                       if d.is_dir() and re.fullmatch(r"S\d+", d.name, re.I)), key=tri_naturel)
    for d in dossiers:
        livre = int(d.name[1:])
        if livres and livre not in livres:
            continue
        for rang, f in enumerate(sorted(d.glob("*.mkv"), key=tri_naturel), 1):
            eps.append(analyser_nom(f, livre, rang))
    return eps


def creer_transcripteur(backend, modele):
    """Renvoie (fonction(chemin) -> liste de segments, nom_du_moteur)."""
    if backend == "auto":
        apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
        backend = "mlx" if apple_silicon else "faster"

    if backend == "mlx":
        import mlx_whisper
        repo = modele or "mlx-community/whisper-large-v3-turbo"

        def run(chemin):
            r = mlx_whisper.transcribe(
                chemin, path_or_hf_repo=repo, language="fr", word_timestamps=True,
                condition_on_previous_text=False)
            return [(s["start"], s["end"], s["text"].strip(),
                     [(w["start"], w["end"], w["word"].strip()) for w in s.get("words", [])])
                    for s in r["segments"]]
        return run, f"mlx:{repo}"

    from faster_whisper import WhisperModel
    nom = modele or "large-v3-turbo"
    m = WhisperModel(nom, device="cpu", compute_type="int8")

    def run(chemin):
        segs, _ = m.transcribe(
            chemin, language="fr", word_timestamps=True, vad_filter=True,
            condition_on_previous_text=False)
        return [(s.start, s.end, s.text.strip(),
                 [(w.start, w.end, w.word.strip()) for w in (s.words or [])])
                for s in segs]
    return run, f"faster-whisper:{nom}"


def enregistrer(con, ep, segments, moteur):
    with con:  # une transaction par épisode : un Ctrl-C ne laisse rien à moitié
        con.execute(
            """INSERT INTO episodes(livre, numero, titre, fichier) VALUES (?,?,?,?)
               ON CONFLICT(fichier) DO UPDATE SET
                 livre=excluded.livre, numero=excluded.numero, titre=excluded.titre""",
            (ep["livre"], ep["numero"], ep["titre"], ep["fichier"]))
        ep_id = con.execute("SELECT id FROM episodes WHERE fichier=?", (ep["fichier"],)).fetchone()[0]
        con.execute("DELETE FROM cues WHERE episode_id=?", (ep_id,))
        for debut, fin, texte, mots in segments:
            if not texte:
                continue
            cue_id = con.execute(
                "INSERT INTO cues(episode_id, debut, fin, texte) VALUES (?,?,?,?)",
                (ep_id, debut, fin, texte)).lastrowid
            con.executemany(
                "INSERT INTO mots(cue_id, idx, debut, fin, mot) VALUES (?,?,?,?,?)",
                [(cue_id, i, d, f, w) for i, (d, f, w) in enumerate(mots)])
        duree = segments[-1][1] if segments else None
        con.execute(
            "UPDATE episodes SET duree=?, moteur=?, transcrit_le=? WHERE id=?",
            (duree, moteur, datetime.datetime.now().isoformat(timespec="seconds"), ep_id))
    return len(segments)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dossier", help="Dossier contenant S01, S02, ...")
    ap.add_argument("--db", default=str(BASE_DEFAUT))
    ap.add_argument("--backend", choices=["auto", "mlx", "faster"], default="auto")
    ap.add_argument("--modele", help="Modèle Whisper (défaut : large-v3-turbo)")
    ap.add_argument("--livre", type=int, action="append", help="Limiter à un Livre (répétable)")
    ap.add_argument("--limite", type=int, help="Nombre max d'épisodes à traiter")
    ap.add_argument("--refaire", action="store_true", help="Re-transcrire les épisodes déjà faits")
    args = ap.parse_args()

    racine = Path(args.dossier).expanduser()
    if not racine.is_dir():
        sys.exit(f"Dossier introuvable : {racine}")

    con = connect(args.db)
    eps = lister_episodes(racine, args.livre)
    if not eps:
        sys.exit("Aucun .mkv trouvé dans des sous-dossiers S01, S02, ...")

    deja = {r[0] for r in con.execute("SELECT fichier FROM episodes WHERE transcrit_le IS NOT NULL")}
    a_faire = [e for e in eps if args.refaire or e["fichier"] not in deja]
    if args.limite:
        a_faire = a_faire[:args.limite]
    nb_faits = sum(e["fichier"] in deja for e in eps)
    print(f"{len(eps)} épisodes trouvés, {nb_faits} déjà transcrits, {len(a_faire)} à traiter.")
    if not a_faire:
        return

    print("Chargement du modèle Whisper (le 1er lancement le télécharge, ~1,5 Go)…")
    transcrire, moteur = creer_transcripteur(args.backend, args.modele)
    print(f"Moteur : {moteur}\n")

    t0 = time.time()
    for i, ep in enumerate(a_faire, 1):
        nom = f"Livre {ep['livre']} – ép. {ep['numero']}" + (f" · {ep['titre']}" if ep["titre"] else "")
        print(f"[{i}/{len(a_faire)}] {nom} … ", end="", flush=True)
        t = time.time()
        try:
            n = enregistrer(con, ep, transcrire(ep["chemin"]), moteur)
        except KeyboardInterrupt:
            print("\nInterrompu. Relance la même commande pour reprendre.")
            return
        except Exception as e:  # un fichier corrompu ne bloque pas le reste
            print(f"ERREUR : {e}")
            continue
        ecoule = time.time() - t0
        restant = ecoule / i * (len(a_faire) - i)
        print(f"{n} répliques en {time.time() - t:.0f}s  (reste ~{restant / 60:.0f} min)")

    total = con.execute("SELECT COUNT(*) FROM cues").fetchone()[0]
    print(f"\nTerminé. {total} répliques dans {args.db}.")


if __name__ == "__main__":
    main()
