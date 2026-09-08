# Aplicativo Desktop

O Transass Desktop é distribuído como AppImage Linux x86_64 e instalador
Windows x64. Ambos contêm Python, QtWebEngine, núcleo do tradutor,
`ffmpeg`/`ffprobe` e recursos da interface.

## Comportamento

- abre uma janela nativa; não abre o navegador na inicialização;
- inicia o Flask em `127.0.0.1` e porta efêmera;
- impede duas instâncias usando lock por processo;
- guarda estado fora da instalação e preserva-o em atualizações;
- usa caminhos nativos por plataforma e aceita pasta com espaços/acentos;
- oferece onboarding, teste de provider e diagnóstico sanitizado.

## Artefatos

- `Transass-x86_64.AppImage`
- `Transass-Setup-2.5.1.exe`
- bundle Windows `build/desktop/Transass/Transass.exe`
- `SHA256SUMS` e `sbom.cdx.json`

O workflow `.github/workflows/desktop.yml` compila em runners Linux e Windows
nativos. O smoke congelado valida versão, manifesto de mídia, `/health` e
onboarding antes de publicar o artefato. “Abriu na máquina do desenvolvedor” é
uma lembrança afetiva, não um teste de release.

Detalhes de build ficam em `desktop/packaging/linux/README.md` e
`desktop/packaging/windows/README.md`.
