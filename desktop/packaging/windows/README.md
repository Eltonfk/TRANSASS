# Empacotamento Windows

Destino planejado: bundle `onedir` + instalador `.exe` por usuário.

Checklist da distribuição:

- build nativo Windows x64 (workflow `desktop.yml`);
- incluir `ffmpeg.exe` e `ffprobe.exe` licenciados e com checksum;
- ícone e atalhos opcionais;
- desinstalador que preserve mídia e estado;
- assinatura do executável, instalador e desinstalador quando certificados
  estiverem disponíveis;
- smoke test em Windows 10/11 sem Python ou Docker.

O build exige `ffmpeg.exe` e `ffprobe.exe` em `TRANSASS_MEDIA_BIN_DIR` (ou no
`PATH` em builds locais) e os coloca em `bin/`. O bundle grava os tamanhos e
SHA-256 em `bin/MEDIA_TOOLS.json`; o aviso de licença fica em `licenses/`.
Para uma distribuição oficial, os binários e os avisos/licenças do fornecedor
devem ser revisados antes da assinatura.

Build local (em um runner Windows com PySide6/PyInstaller/Inno Setup):

```powershell
$env:TRANSASS_MEDIA_BIN_DIR = "C:\\caminho\\para\\media-bin"
python desktop/packaging/build_bundle.py --dist build/desktop
iscc desktop/packaging/windows/installer.iss
python desktop/packaging/checksums.py build/desktop
python desktop/packaging/generate_sbom.py build/desktop
```

O instalador exige privilégios de usuário e o desinstalador remove somente o
`onedir`; estado, credenciais e mídia permanecem no perfil do usuário.
