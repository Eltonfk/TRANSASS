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

O AppDir precisa ser `build/Transass.AppDir` e ainda não existir. O builder
recusa destinos amplos, links e pastas existentes; não apaga builds anteriores
automaticamente. Use um checkout de build limpo ou preserve o build anterior
em outro diretório antes de reconstruir. O CI publica somente instaladores,
checksums e SBOM, não uma segunda cópia do bundle inteiro.

Estado, credenciais e mídia ficam fora do AppImage. O workflow executa o mesmo
processo num runner Linux limpo.
