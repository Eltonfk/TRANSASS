# Instalação

## Desktop Linux

1. Baixe `Transass-x86_64.AppImage` da release.
2. Torne-o executável e abra:

```sh
chmod +x Transass-x86_64.AppImage
./Transass-x86_64.AppImage
```

Escolha a pasta da mídia no onboarding. O aplicativo mantém dados em
`~/.local/share/transass` e configuração em `~/.config/transass`, salvo quando
as variáveis `TRANSASS_DATA_DIR`/`TRANSASS_CONFIG_DIR` definirem outro local.

## Desktop Windows

1. Baixe `Transass-Setup-2.5.1.exe` da release.
2. Execute o instalador para o usuário atual.
3. Abra **Transass** pelo menu Iniciar.
4. Escolha a pasta dos episódios e configure o motor.

O instalador não requer Python, Docker ou Git. Windows pode exibir um alerta
SmartScreen enquanto os binários ainda não possuem assinatura comercial;
confira o SHA-256 publicado antes de continuar.

## Docker

Requisitos: Docker 24+ e Compose v2.

```sh
git clone https://github.com/Eltonfk/TRANSASS.git
cd TRANSASS
cp .env.example .env
```

Edite no mínimo:

```env
MEDIA_ROOT=/caminho/para/series
STATE_DIR=/caminho/para/transass-state
BIND_ADDR=0.0.0.0
WEB_PORT=5050
```

Construa e inicie:

```sh
docker build --pull=false -f deploy/Dockerfile -t subtranslate:v2.5.1 .
docker compose --env-file .env -f deploy/compose.yaml up -d
```

Acesse `http://localhost:5050`. O Compose monta a mídia em `/shows` e o estado
em `/app/state`; esses aliases pertencem ao contêiner, não ao host.

Para Ollama no host Linux, o Compose usa `host.docker.internal`. Confirme antes:

```sh
ollama serve
ollama pull qwen3.5:9b
```

## Desenvolvimento

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock -r requirements-test.lock
PYTHONPATH=.:src/subtranslate python3 src/subtranslate/app.py
```

## Primeira tradução

1. Selecione uma temporada.
2. Carregue os episódios.
3. Confirme a faixa e o idioma detectado pelo conteúdo.
4. Marque os episódios e inicie a fila.
5. Revise o resultado na Caixa de entrada.
6. Publique; o `.pt-BR.ass` substituirá apenas a versão publicada daquele
   episódio, mantendo lineage no acervo.

## Atualização e remoção

- Desktop: instale a versão nova por cima; estado e mídia ficam fora do app.
- Docker: reconstrua a imagem versionada e use `compose up -d --force-recreate`.
- Para parar Docker: `docker compose --env-file .env -f deploy/compose.yaml down`.
- Remover o aplicativo não remove legendas, mídia ou estado automaticamente.

Antes de apagar estado manualmente, faça backup. A pasta contém acervo, fila,
configuração e evidência; ela não é “só um cache” usando bigode falso.

## Problemas comuns

| Sintoma | Verificação |
|---|---|
| Ollama não conecta | `ollama serve`, URL e modelo configurado |
| Pasta vazia | permissões e `MEDIA_ROOT`/pasta escolhida |
| Porta ocupada | altere `WEB_PORT` no Docker; Desktop usa porta efêmera |
| API rejeitada | renove a chave na tela Motor |
| AppImage não abre | permissão executável e log do terminal |
| Windows bloqueia instalação | confirme checksum e use “Mais informações” |
| GPU aquece | veja Diagnóstico, ventilação, driver e Thermal Guard |
