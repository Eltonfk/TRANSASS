# Empacotamento Linux

Destino inicial: AppImage `x86_64`, construído em uma distribuição base antiga
o suficiente para manter compatibilidade com sistemas mais novos.

Checklist da distribuição:

- incluir binários e bibliotecas necessárias;
- desktop entry, ícone e metadados;
- incluir avisos/licenças das dependências;
- checksum e teste em Ubuntu e Linux Mint (workflow `desktop.yml`);
- avaliar pacote `.deb` após o AppImage estar estável.

O workflow prepara uma cópia estática pinada de `ffmpeg`/`ffprobe` em um
diretório temporário e o builder a inclui em `bin/`, com SHA-256 em
`bin/MEDIA_TOOLS.json`. Builds locais devem fornecer `TRANSASS_MEDIA_BIN_DIR`;
sem os dois executáveis o build falha de forma explícita.

Build local:

```sh
python desktop/packaging/build_bundle.py --dist build/desktop
APPIMAGETOOL=/caminho/appimagetool desktop/packaging/linux/build_appimage.sh
```

O AppImage usa o ícone `transass_logo.png`, não requer instalação no sistema e
mantém estado, credenciais e mídia fora do arquivo distribuído.
