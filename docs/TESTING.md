# Testes

## Suíte offline

```sh
PYTHONPATH=.:src/subtranslate python3 -m pytest tests/offline -q
```

Ela não chama modelo nem HTTP externo. Perfis históricos e de estresse ficam
fora do gate padrão e podem ser executados explicitamente com `-m historical`
ou `-m stress`.

## Execução delegada

O CI divide automaticamente os arquivos offline em quatro processos isolados:

```sh
python3 scripts/run_test_shard.py --shard 1 --total 4
```

Repita com shards 1–4 quando quiser reproduzir a matriz. A divisão é completa,
disjunta, determinística e balanceada por custo aproximado; o arquivo de
durabilidade recebe peso próprio porque `fsync` não lê motivational poster.
Cada shard usa seu próprio diretório sintético de estado. No computador local,
a suíte serial continua sendo o comando padrão quando economia de CPU e disco
for mais importante que tempo de parede.

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

- `ci.yml`: quatro shards offline, relatórios JUnit/tempos lentos e build Docker.
  Novos arquivos em `tests/offline/` entram automaticamente no gate remoto.
- `desktop.yml`: matriz Linux/Windows, bundle congelado, smoke, AppImage,
  instalador Inno Setup, checksums e SBOM.

Testes com GPU/provider real ficam em `tests/model/`. Execute-os conscientemente
e com orçamento definido; uma API paga não entende a expressão “foi sem querer”.
