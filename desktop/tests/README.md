# Testes Desktop

```sh
PYTHONPATH=.:src/subtranslate:desktop/src python -m pytest desktop/tests -q
python desktop/tests/run_beta_smoke.py
python desktop/tests/run_bundle_smoke.py build/desktop/Transass/Transass
```

A suíte cobre caminhos Linux/Windows, lock de instância, migração, runtime,
empacotamento e identidade de versão. O smoke congelado valida executável,
manifesto de mídia, `/health` e onboarding sem chamar modelo.

Validação manual de instalação/atualização/desinstalação está em
`desktop/BETA_CHECKLIST.md`.
