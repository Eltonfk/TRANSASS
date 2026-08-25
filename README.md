# Transass

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-216%20offline%20passing-brightgreen.svg)](tests/offline)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](deploy/Dockerfile)

**Transass** traduz legendas de anime para português do Brasil.

Sobre o nome: **Trans** é de *translation* — aqui a gente troca o **idioma**,
não o gênero (respeitamos todos, inclusive os zumbis). E **ass**? ... é bunda.
Não pergunte, foi o que o dono escolheu. O que importa é o que ele faz:

- Traduz episódios inteiros de `.ass`/`.ssa` com **durabilidade forense**:
  cada lote tem ledger, tentativa física e cobertura derivada — **zero retries
  silenciosos** (se falhou, você vai saber).
- **Motor de tradução escolhível**: Ollama local (GPU ou CPU), Gemini (grátis)
  ou qualquer API OpenAI-compatível (Groq, OpenRouter, LM Studio...).
- **Fallback automático**: se o motor principal falhar um lote, o alternativo
  tenta sozinho — como um plano B, mas sem drama.
- **Interface web** com fila segura, Biblioteca com lineage, revisão humana e
  publicação direta no Jellyfin (`.pt-BR.ass` ao lado dos vídeos).

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
- **Idioma de origem configurável** — detecta **todos os idiomas** das legendas
  (sidecars e faixas internas do MKV) e deixa você **selecionar no app** qual
  legenda/idioma traduzir para português do Brasil (inglês por padrão; ex.:
  espanhol, japonês, francês). Karaokê/signs/songs preservados por design.

## 🚀 Início rápido

> 📖 Guia completo (requisitos, instalação, desinstalação, solução de
> problemas, FAQ): **[docs/INSTALLATION.md](docs/INSTALLATION.md)**

### Requisitos mínimos

- **Docker** 24+ (caminho recomendado) **ou** Python 3.11+
- 4 GB RAM · 2 GB disco · CPU 2+ núcleos
- **GPU opcional** — sem GPU, use CPU (mais lento) ou uma API gratuita (Gemini)

### Com Docker (recomendado)

```sh
git clone https://github.com/Eltonfk/TRANSASS.git
cd transass
cp .env.example .env          # ajuste MEDIA_ROOT (pasta dos vídeos) e STATE_DIR
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

- [Instalação e desinstalação](docs/INSTALLATION.md)
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