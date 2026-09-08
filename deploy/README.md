# Docker

`Dockerfile` produz a imagem versionada e `compose.yaml` executa o Transass sem
montar código-fonte. Apenas mídia e estado entram como volumes.

```sh
cp .env.example .env
docker build --pull=false -f deploy/Dockerfile -t subtranslate:v2.5.1 .
docker compose --env-file .env -f deploy/compose.yaml config
docker compose --env-file .env -f deploy/compose.yaml up -d
curl -fsS http://127.0.0.1:5050/health
```

O contêiner é read-only, remove capabilities, usa usuário não-root e grava
somente em `/shows`, `/app/state` e `/tmp`. O arquivo `.env` e os volumes são
estado do operador e não pertencem ao Git.

Ollama normalmente roda no host e é alcançado por
`host.docker.internal:11434`. O Compose adiciona o alias necessário no Linux;
não crie um contêiner chamado `ollama` só para satisfazer um hostname antigo.
