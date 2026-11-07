#!/bin/bash
# Construit « Kaamelott GIF.app » (macOS) à partir de kaamelott-gif-app.py, dans client/dist/.
# La clé de client/.env est embarquée dans l'application : ne partage pas le .app produit.
#
# Usage : client/build-app.sh
set -euo pipefail
cd "$(dirname "$0")"

NAME="Kaamelott GIF"
PYINSTALLER="pyinstaller==6.22.3"

if [ "$(uname)" != Darwin ]; then
  echo "Ce script construit une application macOS : lance-le sur un Mac." >&2
  exit 1
fi
if ! grep -q '^KAAMELOTT_API_KEY=.' .env 2>/dev/null; then
  echo "client/.env manquant ou sans KAAMELOTT_API_KEY : copie .env.example en .env et mets-y ta clé." >&2
  exit 1
fi

# Environnement virtuel du client, avec PySide6 et PyInstaller
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements-app.txt "$PYINSTALLER"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Icône macOS (.icns) à partir de icon.png, avec les outils intégrés à macOS
mkdir "$work/icon.iconset"
for size in 16 32 128 256 512; do
  sips -z $size $size icon.png --out "$work/icon.iconset/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) icon.png --out "$work/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$work/icon.iconset" -o "$work/icon.icns"

# kaamelott-gif-cli.py est chargé dynamiquement par l'application : PyInstaller ne voit pas
# ses imports, d'où la liste des modules à embarquer.
hidden=()
for module in argparse json re shutil socket ssl subprocess urllib.error urllib.parse urllib.request; do
  hidden+=(--hidden-import "$module")
done

# Le .env, kaamelott-gif-cli.py et icon.png sont placés à côté du script dans l'application,
# là où le client les cherche.
.venv/bin/pyinstaller --noconfirm --log-level WARN --windowed \
  --name "$NAME" \
  --icon "$work/icon.icns" \
  --osx-bundle-identifier fr.ml-sirh.kaamelott-gif \
  --add-data "$PWD/kaamelott-gif-cli.py:." \
  --add-data "$PWD/icon.png:." \
  --add-data "$PWD/.env:." \
  "${hidden[@]}" \
  --distpath "$PWD/dist" --workpath "$work/build" --specpath "$work" \
  kaamelott-gif-app.py

rm -rf "dist/$NAME"  # dossier intermédiaire : seul le .app sert
echo
echo "Application construite : client/dist/$NAME.app ($(du -sh "dist/$NAME.app" | cut -f1))"
echo "Pour l'installer : cp -R \"client/dist/$NAME.app\" /Applications/"
