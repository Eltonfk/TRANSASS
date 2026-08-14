# Configuration

Runtime authorities are documented by name only; no secret values are stored.
The deploy environment is authoritative for `TRANSLATOR_PIPELINE`,
`TRANSLATOR_OLLAMA_MODEL`, fallback/reviewer model names, Ollama URL and
state/media paths. The registry validates the pipeline plan but is not model
authority.

The Llama fallback/reviewer dependency remains active and is not removed in
P2C2. Safe placeholders are in `.env.example`. Persistent state and real
credentials remain outside Git.
