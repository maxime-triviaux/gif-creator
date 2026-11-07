#!/usr/bin/env python3
"""Exporte en GIF le passage d'une réplique, avec le texte incrusté en bas de l'image.

Usage :
  python cli/gif.py "c'est pas faux"                 # menu ↑/↓ s'il y a plusieurs résultats
  python cli/gif.py "c'est pas faux" -r 2            # exporte directement le 2e résultat
  python cli/gif.py "c'est pas faux" --apres 1.5     # prolonge après la réplique
  python cli/gif.py "c'est pas faux" --texte "C'est pas faux." -o faux.gif
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

from db import DEFAULT_DB, connect, search, video_path
from search import BOLD, GREY, RESET, timecode

FONTS = ["/System/Library/Fonts/Supplemental/Impact.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]  # Linux (the API's Docker image)
MAX_LINES = 3
OUTPUT_DIR = "output"
ESC = b"\033"


def load_font(size):
    for path in FONTS:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def wrap_lines(text, font, max_width):
    lines, line = [], ""
    for word in text.split():
        attempt = f"{line} {word}".strip()
        if line and font.getlength(attempt) > max_width:
            lines.append(line)
            line = word
        else:
            line = attempt
    return lines + [line] if line else lines


def draw_caption(text, width, path):
    """Writes a transparent PNG: centred white text with a black outline, meme style."""
    size = width // 13
    while True:  # shrink the font until the text fits the width and MAX_LINES lines
        font = load_font(size)
        outline = max(2, size // 12)
        usable_width = width * 0.94 - 2 * outline
        lines = wrap_lines(text, font, usable_width)
        fits = len(lines) <= MAX_LINES and all(font.getlength(l) <= usable_width for l in lines)
        if fits or size <= width // 30:
            break
        size -= 1

    options = dict(font=font, anchor="ma", align="center", spacing=size // 6, stroke_width=outline)
    wrapped = "\n".join(lines)
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    _, top, _, bottom = measure.multiline_textbbox((width / 2, 0), wrapped, **options)
    image = Image.new("RGBA", (width, bottom - top), (0, 0, 0, 0))
    ImageDraw.Draw(image).multiline_text((width / 2, -top), wrapped, fill="white",
                                         stroke_fill="black", **options)
    image.save(path)


def default_name(result, text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    excerpt = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:40].strip("-")
    return f"L{result['livre']}E{result['numero']:03d}_{int(result['debut'])}s_{excerpt or 'replique'}.gif"


def export_gif(video, start, duration, overlay, width, fps, output, timeout=None):
    """Runs ffmpeg; `timeout` (seconds) stops it with subprocess.TimeoutExpired."""
    filters = (
        f"[0:v]fps={fps},scale=iw*sar:ih,setsar=1,scale={width}:-2:flags=lanczos,setsar=1[video];"
        "[video][1:v]overlay=(W-w)/2:H-h-H*0.04,split[a][b];"
        "[a]palettegen=reserve_transparent=0[palette];"
        "[b][palette]paletteuse=dither=bayer:bayer_scale=4"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(start), "-t", str(duration), "-i", video,
         "-i", overlay, "-filter_complex", filters, "-loop", "0", output],
        check=True, timeout=timeout)


def copy_to_clipboard(path):
    """Copies the GIF in two forms, the file and its data: depending on the application
    receiving the paste, only one of them is accepted."""
    # No "set the clipboard to POSIX file": it empties the clipboard without reporting an error
    script = """
ObjC.import('AppKit');
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard;
  const data = $.NSData.dataWithContentsOfFile(argv[0]);
  if (data.isNil()) return 'failed';
  pb.clearContents;
  if (!pb.writeObjects($.NSArray.arrayWithObject($.NSURL.fileURLWithPath(argv[0])))) return 'failed';
  return pb.setDataForType(data, 'com.compuserve.gif') ? 'ok' : 'failed';
}"""
    command = ["osascript", "-l", "JavaScript", "-e", script, str(path.resolve())]
    try:
        return subprocess.run(command, capture_output=True, text=True).stdout.strip() == "ok"
    except OSError:
        return False


def truncate(text, width):
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def read_key(fd):
    """Reads one key; an arrow arrives as a sequence such as ESC [ A."""
    key = os.read(fd, 1)
    if key != ESC or not select.select([fd], [], [], 0.05)[0]:
        return key  # nothing follows: Escape on its own
    key += os.read(fd, 1)
    if key[1:] not in (b"[", b"O"):
        return key
    while True:
        rest = os.read(fd, 1)
        key += rest
        if not rest or 0x40 <= rest[0] <= 0x7e:
            return key


def choose(lines):
    """↑/↓ menu in the terminal. Returns the chosen number (from 1), or None if cancelled."""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    selected, top, drawn = 0, 0, 0
    write = sys.stdout.write
    write("\033[?25l")  # hide the cursor
    try:
        tty.setcbreak(fd)
        while True:
            # A line must never wrap: redrawing relies on the number of lines displayed
            columns, rows = shutil.get_terminal_size()
            visible = max(1, min(len(lines), rows - 2))
            top = min(max(top, selected - visible + 1), selected, len(lines) - visible)
            title = f"Réplique à exporter ({selected + 1}/{len(lines)}) · ↑/↓ choisir · Entrée valider · q annuler"
            screen = [GREY + truncate(title, columns - 1) + RESET]
            for i in range(top, top + visible):
                text = truncate(lines[i], columns - 3)
                screen.append(f"{BOLD}❯ {text}{RESET}" if i == selected else f"  {text}")
            if drawn > 1:
                write(f"\033[{drawn - 1}A")
            write("\r\033[J" + "\n".join(screen))
            sys.stdout.flush()
            drawn = len(screen)

            key = read_key(fd)
            if key in (b"\033[A", b"\033OA"):
                selected = (selected - 1) % len(lines)
            elif key in (b"\033[B", b"\033OB"):
                selected = (selected + 1) % len(lines)
            elif key in (b"\r", b"\n"):
                return selected + 1
            elif key in (b"q", b"Q", ESC, b"\x04", b""):
                return None
    except KeyboardInterrupt:
        return None
    finally:  # always give the terminal back its normal state
        if drawn > 1:
            write(f"\033[{drawn - 1}A")
        write("\r\033[J\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", metavar="requete", help="Réplique à chercher")
    ap.add_argument("-r", "--resultat", dest="result", type=int, metavar="N",
                    help="N° du résultat à exporter, sans poser la question")
    ap.add_argument("-n", metavar="N", dest="limit", type=int, default=15, help="Nombre max de résultats proposés (défaut : 15)")
    ap.add_argument("-o", "--sortie", metavar="SORTIE", dest="output", help="Nom du GIF, placé dans output/ (écrasé s'il existe)")
    ap.add_argument("--texte", metavar="TEXTE", dest="text", help="Texte à afficher à la place de la réplique transcrite")
    ap.add_argument("--avant", metavar="AVANT", dest="before", type=float, default=0.2,
                    help="Secondes gardées avant la réplique (défaut : 0.2)")
    ap.add_argument("--apres", metavar="APRES", dest="after", type=float, default=0.4,
                    help="Secondes gardées après la réplique (défaut : 0.4)")
    ap.add_argument("--largeur", metavar="LARGEUR", dest="width", type=int, default=480, help="Largeur du GIF en pixels (défaut : 480)")
    ap.add_argument("--fps", type=int, default=12, help="Images par seconde (défaut : 12)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    results = search(connect(args.db), args.query, max(args.limit, args.result or 0))
    if not results:
        sys.exit("Aucun résultat.")
    lines = [f"Livre {r['livre']} – ép. {r['numero']}  {timecode(r['debut'])}  {r['texte']}" for r in results]

    number = args.result
    if number is None and len(results) == 1:
        number = 1
    elif number is None and sys.stdin.isatty() and sys.stdout.isatty():
        number = choose(lines)
        if number is None:
            print("Annulé.")
            return
    elif number is None:
        print("\n".join(f"[{i}] {l}" for i, l in enumerate(lines, 1)))
        sys.exit("Plusieurs résultats : précise lequel exporter avec -r N.")
    if not 1 <= number <= len(results):
        sys.exit(f"Pas de résultat n° {number} : {len(results)} résultat(s) seulement.")
    print(f"▶ {lines[number - 1]}")

    r = results[number - 1]
    video = video_path(r["fichier"])
    if not video.is_file():
        sys.exit(f"Fichier introuvable : {video}")
    text = args.text or r["texte"]
    start = max(0.0, r["debut"] - args.before)
    duration = r["fin"] + args.after - start
    output = Path(OUTPUT_DIR) / (args.output or default_name(r, text))  # an absolute path is kept as is
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        overlay = str(Path(tmp) / "caption.png")
        draw_caption(text, args.width, overlay)
        try:
            export_gif(str(video), start, duration, overlay, args.width, args.fps, str(output))
        except subprocess.CalledProcessError:
            sys.exit("Échec de ffmpeg (message ci-dessus).")
    size = output.stat().st_size / 1e6
    print(f"\nGIF écrit : {output}  ({duration:.1f} s, {size:.1f} Mo)")
    # The GIF is already written: a failed copy is not an export error
    print("Copié dans le presse-papier." if copy_to_clipboard(output)
          else "Copie dans le presse-papier impossible.")


if __name__ == "__main__":
    main()
