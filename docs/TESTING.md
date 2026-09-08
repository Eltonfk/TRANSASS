# Testes

## Suíte offline

```sh
PYTHONPATH=.:src/subtranslate python3 -m pytest tests/offline -q
```

Ela não chama modelo nem HTTP externo. Perfis históricos e de estresse ficam
fora do gate padrão e podem ser executados explicitamente com `-m historical`
ou `-m stress`.

## Desktop

```sh
PYTHONPATH=.:src/subtranslate:desktop/src python3 -m pytest desktop/tests -q
python3 desktop/tests/run_beta_smoke.py
python3 desktop/tests/run_bundle_smoke.py build/desktop/Transass/Transass
```

No Windows, o último caminho resolve automaticamente `Transass.exe`. O smoke
confere versão, manifesto `ffmpeg/ffprobe`, servidor local, `/health` e
onboarding, sem tradução real.

## CI

- `ci.yml`: testes offline e build Docker.
- `desktop.yml`: matriz Linux/Windows, bundle congelado, smoke, AppImage,
  instalador Inno Setup, checksums e SBOM.

Testes com GPU/provider real ficam em `tests/model/`. Execute-os conscientemente
e com orçamento definido; uma API paga não entende a expressão “foi sem querer”.
