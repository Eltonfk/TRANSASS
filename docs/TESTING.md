# Testes

## Suíte offline

```sh
scripts/test-offline.sh
```

Ela não chama modelo nem HTTP externo. Perfis históricos e de estresse ficam
fora do gate padrão e podem ser executados explicitamente com `-m historical`
ou `-m stress`.

O atalho e `scripts/run_offline_tests.py` delegam ao mesmo coletor `pytest`
usado no CI, inclusive funções livres e parametrizações. Coleta vazia é erro,
não aprovação. Node.js 22 é usado no CI para testes comportamentais do frontend;
ele não é uma dependência de execução do aplicativo.

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
  instalador Inno Setup, checksums e SBOM. Os artefatos contêm somente arquivos
  da release, sem duplicar o bundle de desenvolvimento, e ficam 14 dias no CI.
  Instaladores publicados na release não dependem dessa retenção temporária.

Antes de uma tag, execute `python3 scripts/check_release.py --ref v3.0.1`.
O gate compara a versão canônica com manifests de Python, Desktop, Windows,
AppStream e imagem selecionada no Docker. O Windows deve passar instalação e
desinstalação com sentinelas de dados; o Linux testa o entry point do AppImage
e o runtime extraído, além do bundle congelado. Isso não é uma tradução real
nem teste de cada hardware de usuário.

Testes com GPU/provider real ficam em `tests/model/`. Execute-os conscientemente
e com orçamento definido; uma API paga não entende a expressão “foi sem querer”.
