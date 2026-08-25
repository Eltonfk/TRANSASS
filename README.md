# Transass

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-216%20offline%20passing-brightgreen.svg)](tests/offline)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](deploy/Dockerfile)

**Transass** é um serviço de tradução de legendas de anime com pipeline canônico,
durabilidade forense por chamada, motor de tradução escolhível (local ou API) e
fallback automático entre motores.

Traduz episódios inteiros de `.ass`/`.ssa` para português do Brasil com
**zero retries silenciosos**: cada lote de tradução tem evidência durável
(ledger, tentativa física, cobertura derivada) e reconciliação canônica.

> Antes chamado de *Subtranslate*, o projeto foi renomeado para **Transass**.

## ✨ Funcionalidades

- **Tradução integral de temporadas** — pipeline V238 com lotes determinísticos
  e evidência forense por chamada (exactly-once, zero retry silencioso).
- **Motor de tradução escolhível** — `ollama` (local/GPU), `openai_compat`
  (Groq, OpenRouter, LM Studio, vLLM, llama.cpp) ou `gemini` (Google).
- **Fallback automático** — se o motor principal falhar um lote, o motor
  alternativo configurado tenta automaticamente (evidência própria por
  tentativa).
- **Interface web** — fila segura de episódios, auditoria, Biblioteca com
  lineage, revisão humana, glossário versionado e memória de tradução.
- **Publicação no Jellyfin** — legendas `.pt-BR.ass` ao lado dos vídeos com a
  nomenclatura correta.
- **Segurança por design** — API keys nunca expostas pela API, armazenadas com
  permissão `600` em arquivo host-local; path traversal bloqueado.

## 🚀 Início rápido

### Com Docker (recomendado)

```sh
cp .env.example .env          # ajuste as variáveis
docker build --pull=false -f deploy/Dockerfile -t transass:latest .
docker compose -f deploy/compose.yaml up -d
# UI em http://localhost:5050
```

### Sem Docker (desenvolvimento)

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
PYTHONPATH=src/subtranslate python3 src/subtranslate/app.py
```

### Testes offline

```sh
PYTHONPATH=src/subtranslate python3 -m pytest tests/offline
```

## ⚙️ Configuração do motor de tradução

Na UI, o botão **⚙ Motor** configura o motor principal + fallback opcional.
Equivalente em arquivo (`transport_config.json` no state dir):

```json
{
  "primary": {"provider": "ollama", "model": "qwen3.5:9b"},
  "fallback": {"provider": "gemini", "model": "gemini-3.6-flash"},
  "keys": {"gemini": "SUA_API_KEY"}
}
```

| Provider | Exemplo de modelo | Key (env ou arquivo) |
|---|---|---|
| `ollama` | `qwen3.5:9b` | — |
| `openai_compat` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` / `OPENROUTER_API_KEY` |
| `gemini` | `gemini-3.6-flash` | `GEMINI_API_KEY` |

> 🔒 **Nunca** coloque keys em arquivos versionados. O `.gitignore` bloqueia
> `*api_key*`, `.env` e `secrets/`.

## 📁 Estrutura

```
src/subtranslate/        # núcleo do pipeline (imports planos, PYTHONPATH)
  app.py                 # interface web (Flask)
  pipeline_v2_1_3.py     # pipeline canônico + Client durável
  transport_providers.py # motores plugáveis (ollama/openai_compat/gemini)
  transport_config_store.py # persistência segura da config de motor
  v238_*.py              # módulos do pipeline V238
tests/offline/           # suítes offline determinísticas (216 testes)
deploy/                  # Dockerfile + compose
docs/                    # arquitetura, operações, segurança, roadmap
```

## 📚 Documentação

- [Arquitetura](docs/ARCHITECTURE.md)
- [Pipelines](docs/PIPELINES.md)
- [Configuração](docs/CONFIGURATION.md)
- [Operações](docs/OPERATIONS.md)
- [Segurança](docs/SECURITY.md)
- [Testes](docs/TESTING.md)
- [Recovery e rollback](docs/RECOVERY_AND_ROLLBACK.md)
- [Biblioteca e lineage](docs/LIBRARY_AND_LINEAGE.md)

## 🤝 Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md) e o
[Código de Conduta](CODE_OF_CONDUCT.md).

## 🔒 Segurança

Encontrou uma vulnerabilidade? Veja [SECURITY.md](SECURITY.md) para a política
de divulgação responsável.

## 📄 Licença

[MIT](LICENSE)