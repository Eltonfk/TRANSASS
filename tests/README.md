# Test boundaries

`tests/offline/` é a suíte canônica padrão. Ela não pode chamar Ollama, HTTP
externo nem estado de produção. Execute na raiz:

```sh
PYTHONPATH=.:src/subtranslate python3 -m pytest tests/offline -q
```

O `pytest.ini` exclui por padrão testes marcados como `historical` e `stress`.
Eles continuam disponíveis de forma explícita:

```sh
PYTHONPATH=.:src/subtranslate python3 -m pytest -m historical tests/offline
PYTHONPATH=.:src/subtranslate python3 -m pytest -m stress tests/offline
```

Probes em `tests/model/` exigem modelo/ambiente autorizado e ficam fora da
validação offline. O teste que diz “offline” e liga para a internet está apenas
tentando ganhar um nome artístico; trate como bug.
