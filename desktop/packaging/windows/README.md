# Build Windows

O build Windows precisa de um host/runner Windows x64; PyInstaller não faz
cross-build de Windows no Linux. O workflow oficial prepara `ffmpeg.exe` e
`ffprobe.exe`, cria `Transass.exe`, executa o smoke congelado e empacota
`Transass-Setup-2.5.1.exe` com Inno Setup.

```powershell
$env:TRANSASS_MEDIA_BIN_DIR = "C:\caminho\para\media-bin"
python desktop/packaging/make_icon.py build/transass.ico
python desktop/packaging/build_bundle.py --dist build/desktop
python desktop/tests/run_bundle_smoke.py build/desktop/Transass/Transass
iscc desktop/packaging/windows/installer.iss
python desktop/packaging/checksums.py build/desktop
python desktop/packaging/generate_sbom.py build/desktop
```

O instalador é por usuário, cria atalhos opcionais e não remove estado ou mídia
na desinstalação. Assinatura depende de certificado protegido.
