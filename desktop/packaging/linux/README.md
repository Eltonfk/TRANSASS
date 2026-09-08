# Build Linux

O artefato oficial é `Transass-x86_64.AppImage`. O builder exige
`ffmpeg`/`ffprobe`, cria o bundle PyInstaller, inclui ícone/desktop entry e
gera `build/release/SHA256SUMS`.

```sh
export TRANSASS_MEDIA_BIN_DIR=/caminho/para/media-bin
python desktop/packaging/build_bundle.py --dist build/desktop
APPIMAGETOOL=/caminho/appimagetool desktop/packaging/linux/build_appimage.sh
python desktop/tests/run_bundle_smoke.py build/desktop/Transass/Transass
```

O empacotador compara a versão do bundle com `_version.py` e recusa um bundle
antigo. Assim um arquivo recém-criado não sai para passear usando a versão de
ontem.

Estado, credenciais e mídia ficam fora do AppImage. O workflow executa o mesmo
processo num runner Linux limpo.
