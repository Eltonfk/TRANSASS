# Instalação do Transass

Guia passo a passo para instalar, configurar, usar e **remover** o Transass em
qualquer máquina.

---

## 1. Requisitos de sistema

### Mínimos (qualquer máquina moderna)

| Recurso | Mínimo | Recomendado |
|---|---|---|
| Sistema operacional | Linux, macOS ou Windows (WSL2) | Linux |
| CPU | 2 núcleos | 4+ núcleos |
| RAM | 4 GB | 8 GB+ |
| Disco | 2 GB livres | 10 GB+ (para modelos e legendas) |
| Python | 3.11+ (instalação manual) | 3.11+ |
| Docker | 24+ (instalação via Docker) | 24+ |
| Internet | Necessária para baixar a imagem e (opcional) APIs | — |

### Para tradução local (Ollama)

| Recurso | Sem GPU (CPU) | Com GPU |
|---|---|---|
| Velocidade | Lenta (~2–4× mais que GPU) | Rápida |
| GPU | — | NVIDIA com 8–12 GB VRAM (ex.: RTX 3060) |
| Modelo sugerido | `qwen3.5:4b` ou `qwen3.5:7b` | `qwen3.5:9b` |

> **Sem GPU?** Você ainda pode traduzir: use o Ollama em CPU (mais lento) ou
> configure uma **API gratuita** (Gemini) — funciona em qualquer máquina com
> internet, sem GPU.

---

## 2. Instalação com Docker (recomendado)

### 2.1 Pré-requisitos

1. Instale o [Docker](https://docs.docker.com/get-docker/) (e Docker Compose,
   incluído no Docker Desktop / plugin do Docker Engine).
2. (Opcional) Instale o [Ollama](https://ollama.com/download) para tradução
   local e baixe um modelo:

   ```sh
   ollama pull qwen3.5:9b
   ```

### 2.2 Baixar o projeto

```sh
git clone https://github.com/Eltonfk/TRANSASS.git
cd transass
```

### 2.3 Configurar

```sh
cp .env.example .env
```

Edite o `.env` com seus valores:

```env
# Pasta com seus vídeos/séries (onde estão os .mkv)
MEDIA_ROOT=/caminho/para/suas/series

# Pasta onde o app guarda o estado (Library, filas, config)
STATE_DIR=/caminho/para/state

# Endereço de acesso (0.0.0.0 = rede local; 127.0.0.1 = só esta máquina)
BIND_ADDR=0.0.0.0
WEB_PORT=5050

# Motor de tradução
TRANSPORT_PROVIDER=ollama
TRANSPORT_MODEL=qwen3.5:9b
```

### 2.4 Construir e iniciar

```sh
docker build --pull=false -f deploy/Dockerfile -t transass:latest .
docker compose -f deploy/compose.yaml up -d
```

Acesse a interface em **http://localhost:5050**.

### 2.5 Configurar o motor de tradução (na UI)

Clique em **⚙ Motor** e escolha:

- **Ollama local**: provider `ollama`, modelo `qwen3.5:9b` (sem key).
- **Gemini (grátis)**: provider `gemini`, modelo `gemini-3.6-flash`, cole sua
  API key (obtenha em [aistudio.google.com](https://aistudio.google.com)).
- **Groq/OpenRouter**: provider `openai_compat` + base_url + key.

O **fallback** é opcional: se o motor principal falhar um lote, o alternativo
tenta automaticamente.

**Idioma de origem**: no mesmo diálogo ⚙ Motor, o campo **"Idioma de origem da
legenda"** define de qual idioma a legenda fonte será traduzida (destino sempre
português do Brasil). Padrão: `inglês`. Exemplos válidos: `espanhol`,
`japonês`, `francês`, `coreano`. Karaokê e signs/songs são preservados
automaticamente (não são traduzidos). Também pode ser definido via variável de
ambiente `TRANSLATOR_SOURCE_LANGUAGE` no `.env`.

> Nota: a resolução automática de fonte no MKV ainda prioriza a track/texto em
> inglês quando disponível; para outros idiomas de origem, forneça um sidecar
> (ex.: `episodio.esp.ass`) ou selecione a track explicitamente.

---

## 3. Instalação manual (sem Docker)

```sh
git clone https://github.com/Eltonfk/TRANSASS.git
cd transass

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.lock

cp .env.example .env             # ajuste os paths
export PYTHONPATH=src/subtranslate
python3 src/subtranslate/app.py
```

Acesse **http://localhost:5050**.

---

## 4. Como usar (primeira tradução)

1. Abra a interface web.
2. Navegue até a pasta com seus episódios (ex.: `Zombie Land Saga/Season 1`).
3. Selecione os episódios sem legenda PT-BR.
4. Clique em **Traduzir** — a fila processa com segurança (sem sobrescrever
   legendas existentes).
5. As legendas `.pt-BR.ass` são geradas ao lado dos vídeos, prontas para o
   Jellyfin/Plex reconhecerem automaticamente.

---

## 5. Desinstalação

### Parar e remover o container (Docker)

```sh
docker compose -f deploy/compose.yaml down
```

### Remover a imagem

```sh
docker rmi transass:latest
```

### Remover o estado (opcional — apaga Library, filas e config de motor)

```sh
# CUIDADO: apaga o estado persistente do app
rm -rf ./state
```

### Remover tudo (projeto + dados)

```sh
cd ..
rm -rf transass
```

> As legendas `.pt-BR.ass` geradas **não** são apagadas — ficam na pasta dos
> seus vídeos. Remova-as manualmente se desejar.

---

## 6. Solução de problemas

| Problema | Causa provável | Solução |
|---|---|---|
| `Porta 5050 já em uso` | Outro app na porta | Mude `WEB_PORT` no `.env` |
| `Não consigo acessar de outro dispositivo` | `BIND_ADDR` restrito | Use `BIND_ADDR=0.0.0.0` |
| `Ollama não conecta` | URL errada / Ollama não rodando | Confirme `TRANSLATOR_OLLAMA_URL` e que `ollama serve` está ativo |
| Tradução muito lenta | CPU sem GPU | Use modelo menor ou API (Gemini) |
| `API key inválida` | Key errada/expirada | Regere em aistudio.google.com |
| Legenda não aparece no Jellyfin | Scan não rodou | Atualize metadados / aguarde o scan |
| Erro ao buildar | Rede bloqueada | Use `docker build --pull=false` com base já baixada |

---

## 7. FAQ

**Preciso de GPU?**
Não. GPU acelera o Ollama local, mas você pode usar CPU (mais lento) ou uma
API gratuita (Gemini) sem GPU.

**Preciso pagar algo?**
Não. Ollama é local e gratuito; Gemini tem tier gratuito (limites diários).

**Funciona no Windows?**
Sim — com Docker Desktop ou WSL2. A instalação manual usa `venv` nativo.

**O app sobrescreve minhas legendas existentes?**
Não. A fila é segura: nunca sobrescreve legendas PT-BR existentes.

**Onde ficam minhas legendas traduzidas?**
Ao lado dos vídeos, com o nome `<vídeo>.pt-BR.ass` — o Jellyfin/Plex
reconhece automaticamente.

**Posso usar mais de um motor?**
Sim — motor principal + fallback automático (se o principal falhar, o
alternativo tenta).

**O que acontece com minhas API keys?**
Ficam em arquivo local com permissão `600`, nunca expostas pela interface nem
versionadas no Git.