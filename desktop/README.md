# Transass Desktop

O Desktop 2.5.1 incorpora a interface Flask numa janela PySide6/QtWebEngine e
compartilha o mesmo núcleo do Docker.

```text
desktop/src/transass_desktop/   launcher, caminhos, lock e runtime
desktop/packaging/              PyInstaller, AppImage e Inno Setup
desktop/tests/                  testes e smoke congelado
```

O aplicativo usa porta localhost efêmera, guarda dados fora do executável e
preserva mídia/estado durante atualização e desinstalação. Links externos só
abrem por ação explícita no menu Ajuda.

Builds oficiais são nativos por sistema operacional no workflow
`desktop.yml`. Consulte `docs/DESKTOP.md` e os READMEs de cada empacotador.
