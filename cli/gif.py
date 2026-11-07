#!/usr/bin/env python3
"""Exporte en GIF le passage d'une réplique, avec le texte incrusté en bas de l'image.

Usage :
  python gif.py "c'est pas faux"                 # menu ↑/↓ s'il y a plusieurs résultats
  python gif.py "c'est pas faux" -r 2            # exporte directement le 2e résultat
  python gif.py "c'est pas faux" --apres 1.5     # prolonge après la réplique
  python gif.py "c'est pas faux" --texte "C'est pas faux." -o faux.gif
"""
import argparse
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import tty
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from db import BASE_DEFAUT, chemin_video, connect, rechercher
from search import FIN, GRAS, GRIS, tc

POLICES = ["/System/Library/Fonts/Supplemental/Impact.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]  # Linux (image Docker de l'API)
MAX_LIGNES = 3
DOSSIER_SORTIE = "output"
ECHAP = b"\033"


def charger_police(taille):
    for chemin in POLICES:
        if Path(chemin).is_file():
            return ImageFont.truetype(chemin, taille)
    return ImageFont.load_default(taille)


def couper_lignes(texte, police, largeur_max):
    lignes, ligne = [], ""
    for mot in texte.split():
        essai = f"{ligne} {mot}".strip()
        if ligne and police.getlength(essai) > largeur_max:
            lignes.append(ligne)
            ligne = mot
        else:
            ligne = essai
    return lignes + [ligne] if ligne else lignes


def dessiner_texte(texte, largeur, chemin):
    """Écrit un PNG transparent : texte blanc centré, contour noir, en style mème."""
    taille = largeur // 13
    while True:  # réduit la police jusqu'à tenir dans la largeur et sur MAX_LIGNES lignes
        police = charger_police(taille)
        contour = max(2, taille // 12)
        largeur_utile = largeur * 0.94 - 2 * contour
        lignes = couper_lignes(texte, police, largeur_utile)
        tient = len(lignes) <= MAX_LIGNES and all(police.getlength(l) <= largeur_utile for l in lignes)
        if tient or taille <= largeur // 30:
            break
        taille -= 1

    options = dict(font=police, anchor="ma", align="center", spacing=taille // 6, stroke_width=contour)
    texte_coupe = "\n".join(lignes)
    mesure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    _, haut, _, bas = mesure.multiline_textbbox((largeur / 2, 0), texte_coupe, **options)
    image = Image.new("RGBA", (largeur, bas - haut), (0, 0, 0, 0))
    ImageDraw.Draw(image).multiline_text((largeur / 2, -haut), texte_coupe, fill="white",
                                         stroke_fill="black", **options)
    image.save(chemin)


def nom_par_defaut(r, texte):
    ascii_ = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    extrait = re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:40].strip("-")
    return f"L{r['livre']}E{r['numero']:03d}_{int(r['debut'])}s_{extrait or 'replique'}.gif"


def exporter(fichier, debut, duree, calque, largeur, fps, sortie, delai=None):
    """Lance ffmpeg ; `delai` (secondes) l'interrompt avec subprocess.TimeoutExpired."""
    filtres = (
        f"[0:v]fps={fps},scale=iw*sar:ih,setsar=1,scale={largeur}:-2:flags=lanczos,setsar=1[video];"
        "[video][1:v]overlay=(W-w)/2:H-h-H*0.04,split[a][b];"
        "[a]palettegen=reserve_transparent=0[palette];"
        "[b][palette]paletteuse=dither=bayer:bayer_scale=4"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(debut), "-t", str(duree), "-i", fichier,
         "-i", calque, "-filter_complex", filtres, "-loop", "0", sortie],
        check=True, timeout=delai)


def copier_presse_papier(chemin):
    """Copie le GIF sous deux formes : le fichier et ses données, car selon l'application
    qui reçoit le collage, seule l'une des deux est acceptée."""
    # Pas de « set the clipboard to POSIX file » : il vide le presse-papier sans signaler d'erreur
    script = """
ObjC.import('AppKit');
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard;
  const donnees = $.NSData.dataWithContentsOfFile(argv[0]);
  if (donnees.isNil()) return 'echec';
  pb.clearContents;
  if (!pb.writeObjects($.NSArray.arrayWithObject($.NSURL.fileURLWithPath(argv[0])))) return 'echec';
  return pb.setDataForType(donnees, 'com.compuserve.gif') ? 'ok' : 'echec';
}"""
    commande = ["osascript", "-l", "JavaScript", "-e", script, str(chemin.resolve())]
    try:
        return subprocess.run(commande, capture_output=True, text=True).stdout.strip() == "ok"
    except OSError:
        return False


def couper_ecran(texte, largeur):
    return texte if len(texte) <= largeur else texte[:max(0, largeur - 1)] + "…"


def lire_touche(fd):
    """Lit une touche ; une flèche arrive sous forme d'une séquence comme ESC [ A."""
    touche = os.read(fd, 1)
    if touche != ECHAP or not select.select([fd], [], [], 0.05)[0]:
        return touche  # select vide : Échap seul, sans séquence derrière
    touche += os.read(fd, 1)
    if touche[1:] not in (b"[", b"O"):
        return touche
    while True:
        suite = os.read(fd, 1)
        touche += suite
        if not suite or 0x40 <= suite[0] <= 0x7e:
            return touche


def choisir(lignes):
    """Menu ↑/↓ dans le terminal. Renvoie le numéro choisi (dès 1), ou None si annulé."""
    fd = sys.stdin.fileno()
    reglages = termios.tcgetattr(fd)
    choix, haut, dessinees = 0, 0, 0
    ecrire = sys.stdout.write
    ecrire("\033[?25l")  # cache le curseur
    try:
        tty.setcbreak(fd)
        while True:
            # Une ligne ne doit jamais déborder : le redessin compte les lignes affichées
            colonnes, rangees = shutil.get_terminal_size()
            visibles = max(1, min(len(lignes), rangees - 2))
            haut = min(max(haut, choix - visibles + 1), choix, len(lignes) - visibles)
            titre = f"Réplique à exporter ({choix + 1}/{len(lignes)}) · ↑/↓ choisir · Entrée valider · q annuler"
            ecran = [GRIS + couper_ecran(titre, colonnes - 1) + FIN]
            for i in range(haut, haut + visibles):
                texte = couper_ecran(lignes[i], colonnes - 3)
                ecran.append(f"{GRAS}❯ {texte}{FIN}" if i == choix else f"  {texte}")
            if dessinees > 1:
                ecrire(f"\033[{dessinees - 1}A")
            ecrire("\r\033[J" + "\n".join(ecran))
            sys.stdout.flush()
            dessinees = len(ecran)

            touche = lire_touche(fd)
            if touche in (b"\033[A", b"\033OA"):
                choix = (choix - 1) % len(lignes)
            elif touche in (b"\033[B", b"\033OB"):
                choix = (choix + 1) % len(lignes)
            elif touche in (b"\r", b"\n"):
                return choix + 1
            elif touche in (b"q", b"Q", ECHAP, b"\x04", b""):
                return None
    except KeyboardInterrupt:
        return None
    finally:  # le terminal doit retrouver son état normal, quoi qu'il arrive
        if dessinees > 1:
            ecrire(f"\033[{dessinees - 1}A")
        ecrire("\r\033[J\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, reglages)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("requete", help="Réplique à chercher")
    ap.add_argument("-r", "--resultat", type=int, metavar="N", help="N° du résultat à exporter, sans poser la question")
    ap.add_argument("-n", type=int, default=15, help="Nombre max de résultats proposés (défaut : 15)")
    ap.add_argument("-o", "--sortie", help="Nom du GIF, placé dans output/ (écrasé s'il existe)")
    ap.add_argument("--texte", help="Texte à afficher à la place de la réplique transcrite")
    ap.add_argument("--avant", type=float, default=0.2, help="Secondes gardées avant la réplique (défaut : 0.2)")
    ap.add_argument("--apres", type=float, default=0.4, help="Secondes gardées après la réplique (défaut : 0.4)")
    ap.add_argument("--largeur", type=int, default=480, help="Largeur du GIF en pixels (défaut : 480)")
    ap.add_argument("--fps", type=int, default=12, help="Images par seconde (défaut : 12)")
    ap.add_argument("--db", default=str(BASE_DEFAUT))
    args = ap.parse_args()

    res = rechercher(connect(args.db), args.requete, max(args.n, args.resultat or 0))
    if not res:
        sys.exit("Aucun résultat.")
    lignes = [f"Livre {r['livre']} – ép. {r['numero']}  {tc(r['debut'])}  {r['texte']}" for r in res]

    numero = args.resultat
    if numero is None and len(res) == 1:
        numero = 1
    elif numero is None and sys.stdin.isatty() and sys.stdout.isatty():
        numero = choisir(lignes)
        if numero is None:
            print("Annulé.")
            return
    elif numero is None:
        print("\n".join(f"[{i}] {l}" for i, l in enumerate(lignes, 1)))
        sys.exit("Plusieurs résultats : précise lequel exporter avec -r N.")
    if not 1 <= numero <= len(res):
        sys.exit(f"Pas de résultat n° {numero} : {len(res)} résultat(s) seulement.")
    print(f"▶ {lignes[numero - 1]}")

    r = res[numero - 1]
    video = chemin_video(r["fichier"])
    if not video.is_file():
        sys.exit(f"Fichier introuvable : {video}")
    texte = args.texte or r["texte"]
    debut = max(0.0, r["debut"] - args.avant)
    duree = r["fin"] + args.apres - debut
    sortie = Path(DOSSIER_SORTIE) / (args.sortie or nom_par_defaut(r, texte))  # un chemin absolu reste tel quel
    sortie.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as dossier:
        calque = str(Path(dossier) / "texte.png")
        dessiner_texte(texte, args.largeur, calque)
        try:
            exporter(str(video), debut, duree, calque, args.largeur, args.fps, str(sortie))
        except subprocess.CalledProcessError:
            sys.exit("Échec de ffmpeg (message ci-dessus).")
    taille = sortie.stat().st_size / 1e6
    print(f"\nGIF écrit : {sortie}  ({duree:.1f} s, {taille:.1f} Mo)")
    # Le GIF est déjà écrit : un échec de copie n'est pas une erreur d'export
    print("Copié dans le presse-papier." if copier_presse_papier(sortie)
          else "Copie dans le presse-papier impossible.")


if __name__ == "__main__":
    main()
