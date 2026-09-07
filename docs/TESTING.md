# Testing

Offline tests are the default and must have zero model and external HTTP
activity. `tests/model/` contains explicit model-required probes and is never
collected by the default `pytest.ini`. Historical superseded expectations are
preserved and exactly deselected by the offline command; they are not deleted,
silently edited or converted into fake passes.

Tests must use synthetic fixtures or approved offline evidence, never the real
production Library, jobs, media or state.

## Unit suite (CI)

The **environment-independent unit suite** runs on GitHub Actions and on any
machine without the author's operational environment:

```sh
PYTHONPATH=src/subtranslate python3 -m pytest \
  tests/offline/test_v238_canonical_port.py \
  tests/offline/test_transport_providers.py \
  tests/offline/test_subtranslate_readonly_probe.py \
  tests/offline/test_subtranslate_context_inspect.py \
  tests/offline/test_subtranslate_next_routing.py \
  tests/offline/test_subtranslate_command_dispatch.py
```

## Operational suite (author environment)

The remaining `tests/offline/` files are **operational tests** that require
the author's canonical state (`PROJECT_STATE.json`), runtime evidence and
machine-specific paths. They are not part of CI and are expected to fail on a
clean checkout without that environment.

## Perfis após o arquivamento do E07

O gate padrão exclui explicitamente os módulos que dependem da evidência E07
arquivada e o teste de stress de 233 chamadas:

```sh
PYTHONPATH=src/subtranslate python3 -m pytest tests/offline
```

Os perfis continuam disponíveis para auditoria explícita:

```sh
PYTHONPATH=src/subtranslate python3 -m pytest -m historical tests/offline
PYTHONPATH=src/subtranslate python3 -m pytest -m stress tests/offline
```

`historical` não significa que os testes foram apagados ou aprovados: sem a
fonte e o ledger arquivados, eles continuam falhando de forma fail-closed.
`stress` mantém a verificação completa de persistência, separada do gate
rápido do produto.
