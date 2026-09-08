#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
bundle="${1:-$root/build/desktop/Transass}"
appdir="${2:-$root/build/Transass.AppDir}"
: "${APPIMAGETOOL:?Defina APPIMAGETOOL apontando para o binário appimagetool}"
expected_version="$(PYTHONPATH="$root/src/subtranslate" python3 -c 'from _version import __version__; print(__version__)')"
bundle_version="$("$bundle/Transass" --version)"
if [[ "$bundle_version" != "Transass $expected_version" ]]; then
  echo "Bundle desatualizado: esperado Transass $expected_version, encontrado $bundle_version" >&2
  echo "Reconstrua build/desktop antes de gerar o AppImage." >&2
  exit 1
fi
rm -rf "$appdir"
mkdir -p "$appdir/usr/bin" "$appdir/usr/share/applications" \
  "$appdir/usr/share/icons/hicolor/256x256/apps" "$appdir/usr/share/metainfo"
cp -a "$bundle/." "$appdir/usr/bin/"
cp "$root/desktop/packaging/linux/io.github.Eltonfk.Transass.desktop" \
  "$appdir/usr/share/applications/io.github.Eltonfk.Transass.desktop"
cp "$root/desktop/packaging/linux/io.github.Eltonfk.Transass.desktop" \
  "$appdir/io.github.Eltonfk.Transass.desktop"
cp "$root/desktop/packaging/linux/io.github.Eltonfk.Transass.appdata.xml" \
  "$appdir/usr/share/metainfo/io.github.Eltonfk.Transass.appdata.xml"
cp "$root/src/subtranslate/transass_logo.png" "$appdir/usr/share/icons/hicolor/256x256/apps/transass.png"
cp "$root/src/subtranslate/transass_logo.png" "$appdir/transass.png"
cat > "$appdir/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/usr/bin/Transass" "$@"
EOF
chmod +x "$appdir/AppRun"
output="${3:-$root/build/Transass-x86_64.AppImage}"
"$APPIMAGETOOL" "$appdir" "$output"
release_dir="$root/build/release"
mkdir -p "$release_dir"
cp "$output" "$release_dir/$(basename "$output")"
python3 "$root/desktop/packaging/checksums.py" "$release_dir"
