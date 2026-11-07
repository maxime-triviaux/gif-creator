#!/usr/bin/env python3
"""Cherche une réplique de Kaamelott et l'exporte en GIF, via l'API (sans vidéos ni base en local).

Usage :
  python3 kaamelott-gif-cli.py "c'est pas faux"              # menu ↑/↓ s'il y a plusieurs résultats
  python3 kaamelott-gif-cli.py "c'est pas faux" -r 2         # exporte directement le 2e résultat
  python3 kaamelott-gif-cli.py "graal" -l                    # liste les résultats, sans export
  python3 kaamelott-gif-cli.py "radis" --apres 1.5 --texte "Comme des radis !" -o radis.gif

La clé d'API se lit dans la variable KAAMELOTT_API_KEY, ou dans un fichier .env placé dans le
dossier courant ou à côté de ce script (voir .env.example).
Seule la bibliothèque standard de Python 3 est utilisée : rien à installer.
"""
import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://gif.ml-sirh.fr"
OUTPUT_DIR = "output"
USER_AGENT = "kaamelott-gif-client/1"
MAX_ATTEMPTS = 4  # the API answers 503 when too many GIFs are being rendered at once
GREY, BOLD, RESET = ("\033[90m", "\033[1m", "\033[0m") if sys.stdout.isatty() else ("", "", "")
ESC = b"\033"


# --- Configuration ----------------------------------------------------------------------------

def read_env(path):
    """Reads a simple .env file: KEY=value, # lines ignored, optional quotes."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def load_config():
    """Returns (url, key). Priority: environment variables, .env in the current directory,
    .env next to the script."""
    files = [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    values = {}
    for path in reversed(files):  # the first one in the list wins
        if path.is_file():
            values.update(read_env(path))
    values.update({k: v for k, v in os.environ.items() if k.startswith("KAAMELOTT_")})

    key = values.get("KAAMELOTT_API_KEY", "").strip()
    if not key:
        sys.exit("Clé d'API manquante : ajoute KAAMELOTT_API_KEY=ta-cle dans un fichier .env "
                 "à côté de ce script (voir .env.example).")
    url = (values.get("KAAMELOTT_API_URL") or DEFAULT_URL).strip().rstrip("/")
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        sys.exit(f"KAAMELOTT_API_URL doit commencer par https:// (reçu : {url}).")
    return url, key


# --- API calls --------------------------------------------------------------------------------

class ApiError(Exception):
    pass


MACOS_ROOTS = "/System/Library/Keychains/SystemRootCertificates.keychain"
_ssl_context = None


def ssl_context():
    """TLS context that always verifies the certificate. The python.org build of Python on macOS has
    no root certificates until "Install Certificates.command" has been run: certifi's are then used
    if it is installed, otherwise those of the macOS system keychain."""
    global _ssl_context
    if _ssl_context is None:
        _ssl_context = ssl.create_default_context()
        if _ssl_context.cert_store_stats()["x509_ca"] == 0:
            try:
                import certifi
                _ssl_context.load_verify_locations(certifi.where())
            except ImportError:
                if sys.platform == "darwin" and os.path.exists(MACOS_ROOTS):
                    pem = subprocess.run(["security", "find-certificate", "-a", "-p", MACOS_ROOTS],
                                         capture_output=True, text=True).stdout
                    if pem.strip():
                        _ssl_context.load_verify_locations(cadata=pem)
    return _ssl_context


def api_get(url, key, path, params):
    """GET on the API; returns (body, headers). Retries while the API is saturated (503)."""
    address = f"{url}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(address, headers={"X-API-Key": key, "User-Agent": USER_AGENT})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=150, context=ssl_context()) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as e:
            detail = error_message(e)
            # Retry-After: temporary saturation; without it (unreadable database), retrying is pointless
            retry_after = e.headers.get("Retry-After", "")
            if e.code == 503 and retry_after.isdigit() and attempt < MAX_ATTEMPTS:
                wait = min(int(retry_after), 30)
                print(f"{GREY}API occupée, nouvel essai dans {wait} s…{RESET}", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code == 401:
                raise ApiError("Clé d'API refusée : vérifie KAAMELOTT_API_KEY dans ton .env.")
            raise ApiError(f"Erreur {e.code} de l'API : {detail}")
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
                raise ApiError(f"Certificat de {url} non vérifié : ce Python n'a pas de certificats racines. "
                               "Sur macOS, lance « Install Certificates.command » (dossier Python de "
                               "Applications), ou : pip install certifi")
            raise ApiError(f"API injoignable ({url}) : {e.reason}")
        except (TimeoutError, socket.timeout):  # socket.timeout: Python < 3.10
            raise ApiError("L'API met trop de temps à répondre.")
    raise ApiError("API saturée, réessaie dans un instant.")


def error_message(e):
    try:
        detail = json.loads(e.read()).get("detail")
    except (ValueError, AttributeError, OSError):
        return e.reason
    if isinstance(detail, list):  # FastAPI validation error: [{"loc": …, "msg": …}]
        return " ; ".join(f"{d.get('loc', ['?'])[-1]} : {d.get('msg')}" for d in detail)
    return str(detail or e.reason)


def search(url, key, query, limit):
    body, _ = api_get(url, key, "/repliques", {"q": query, "limit": limit})
    return json.loads(body)


def download_gif(url, key, result, args):
    # Query parameter names are the API's (French) public interface
    params = {"avant": args.before, "apres": args.after, "largeur": args.width, "fps": args.fps}
    if args.text:
        params["texte"] = args.text
    return api_get(url, key, result["gif"], params)


# --- Display ----------------------------------------------------------------------------------

def timecode(seconds):
    minutes, seconds = divmod(round(seconds, 1), 60)
    return f"{int(minutes):02d}:{seconds:04.1f}"


def file_name(headers, result):
    """Name suggested by the API (Content-Disposition), otherwise one built here."""
    m = re.search(r'filename="([^"/\\]+)"', headers.get("Content-Disposition", ""))
    return m.group(1) if m else f"L{result['livre']}E{result['numero']:03d}_{int(result['debut'])}s.gif"


def truncate(text, width):
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def read_key(fd):
    """Reads one key; an arrow arrives as a sequence such as ESC [ A."""
    import select
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


def choose_with_arrows(lines):
    """↑/↓ menu in the terminal. Returns the chosen number (from 1), or None if cancelled."""
    import termios
    import tty
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


def choose_by_number(lines):
    """Without an arrow menu (Windows): numbered list and typed number."""
    print("\n".join(f"[{i}] {l}" for i, l in enumerate(lines, 1)))
    try:
        answer = input("Numéro à exporter (Entrée : annuler) : ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return int(answer) if answer.isdigit() else None


def choose(lines):
    try:
        import termios  # noqa: F401 (missing on Windows)
    except ImportError:
        return choose_by_number(lines)
    return choose_with_arrows(lines)


# --- Clipboard --------------------------------------------------------------------------------

def copy_to_clipboard(path):
    """Copies the GIF to the clipboard: macOS, or Linux with wl-copy / xclip."""
    path = str(path.resolve())
    if sys.platform == "darwin":
        # The file and its data: depending on the application receiving the paste, only one of them
        # is accepted. No "set the clipboard to POSIX file": it empties the clipboard
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
        command = ["osascript", "-l", "JavaScript", "-e", script, path]
        try:
            return subprocess.run(command, capture_output=True, text=True).stdout.strip() == "ok"
        except OSError:
            return False
    for command in (["wl-copy", "--type", "image/gif"], ["xclip", "-selection", "clipboard", "-t", "image/gif"]):
        if shutil.which(command[0]):
            with open(path, "rb") as f:
                return subprocess.run(command, stdin=f).returncode == 0
    return False


# --- Program ----------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", metavar="requete", help="Réplique à chercher")
    ap.add_argument("-r", "--resultat", dest="result", type=int, metavar="N",
                    help="N° du résultat à exporter, sans poser la question")
    ap.add_argument("-n", metavar="N", dest="limit", type=int, default=15,
                    help="Nombre max de résultats proposés (défaut : 15, max : 50)")
    ap.add_argument("-l", "--liste", dest="list_only", action="store_true",
                    help="Affiche les résultats sans exporter de GIF")
    ap.add_argument("-o", "--sortie", metavar="SORTIE", dest="output", help="Nom du GIF, placé dans output/ (écrasé s'il existe)")
    ap.add_argument("--texte", metavar="TEXTE", dest="text",
                    help="Texte à afficher à la place de la réplique transcrite (200 caractères max)")
    ap.add_argument("--avant", metavar="AVANT", dest="before", type=float, default=0.2,
                    help="Secondes gardées avant la réplique (défaut : 0.2, max : 5)")
    ap.add_argument("--apres", metavar="APRES", dest="after", type=float, default=0.4,
                    help="Secondes gardées après la réplique (défaut : 0.4, max : 5)")
    ap.add_argument("--largeur", metavar="LARGEUR", dest="width", type=int, default=480,
                    help="Largeur du GIF en pixels (défaut : 480, de 160 à 720)")
    ap.add_argument("--fps", type=int, default=12, help="Images par seconde (défaut : 12, de 5 à 20)")
    args = ap.parse_args()

    url, key = load_config()
    try:
        results = search(url, key, args.query, min(50, max(1, args.limit, args.result or 0)))
        if not results:
            sys.exit("Aucun résultat.")
        lines = [f"Livre {r['livre']} – ép. {r['numero']}  {timecode(r['debut'])}  {r['texte']}" for r in results]

        if args.list_only:
            for i, (r, line) in enumerate(zip(results, lines), 1):
                print(f"{BOLD}[{i}] {line}{RESET}")
                for following in r.get("apres", []):  # what comes next, to recognise the scene
                    print(f"    {GREY}{following}{RESET}")
            print(f"\n{GREY}Exporter : -r N (ex. -r 1){RESET}")
            return

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
        r = results[number - 1]
        print(f"▶ {lines[number - 1]}")
        print(f"{GREY}Génération du GIF…{RESET}")
        started = time.monotonic()
        data, headers = download_gif(url, key, r, args)
    except ApiError as e:
        sys.exit(str(e))

    output = Path(OUTPUT_DIR) / (args.output or file_name(headers, r))  # an absolute path is kept as is
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(f"\nGIF écrit : {output}  ({len(data) / 1e6:.1f} Mo, {time.monotonic() - started:.1f} s)")
    # The GIF is already written: a failed copy is not an export error
    print("Copié dans le presse-papier." if copy_to_clipboard(output)
          else "Copie dans le presse-papier impossible.")


if __name__ == "__main__":
    main()
