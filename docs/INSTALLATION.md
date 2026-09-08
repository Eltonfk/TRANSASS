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
docker compose --env-file .env -f deploy/compose.yaml up -d
```

Acesse a interface em **http://localhost:5050**.

### 2.5 Configurar o motor de tradução (na UI)

Clique em **⚙ Motor** e escolha:

- **Ollama local**: provider `ollama`, modelo `qwen3.5:9b` (sem key).
- **Gemini**: provider `gemini`, escolha um dos modelos de texto exibidos na
  lista (o padrão recomendado é `gemini-3.5-flash-lite`) e cole sua API key
  (obtenha em [aistudio.google.com](https://aistudio.google.com)). Ao abrir a
  configuração, o Transass consulta os modelos disponíveis para a chave e
  filtra variantes de áudio, imagem, TTS e embedding, que não servem para
  tradução de legendas. Sem conexão com a API, três modelos estáveis seguros
  permanecem disponíveis localmente.

O **perfil API otimizado** é ativado automaticamente quando Gemini é usado:
ele controla o tamanho dos lotes, orçamento de retries e intervalo entre
chamadas, sem alterar o modelo escolhido pelo usuário.
- **Groq**: provider `groq`, modelo de texto exibido e key Groq.
- **OpenRouter/LM Studio**: provider `openai_compat` + base_url + key opcional.
- **DeepSeek**: provider `deepseek`, modelo `deepseek-chat` ou `deepseek-reasoner` e key DeepSeek.
- **NVIDIA NIM**: provider `nvidia`, modelo com namespace (por exemplo
  `meta/llama-3.1-8b-instruct`) e key NVIDIA.

Sem `transport_config.json`, a seleção não secreta de `TRANSPORT_PROVIDER`,
`TRANSPORT_MODEL` e seus equivalentes `TRANSPORT_FALLBACK_*` no `.env` é usada
como configuração inicial. Depois que a configuração é salva pela UI, ela
passa a ter precedência; as API keys continuam sendo resolvidas do keyring,
arquivo local ou ambiente e nunca entram no execution context.

O **fallback** é opcional: se o motor principal ficar indisponível por falha de
transporte, o alternativo tenta automaticamente. Falhas de validação linguística
ou estrutural não trocam silenciosamente de motor; exigem revisão/retry
seletivo.

### Proteção térmica para Ollama em Linux e Windows

Quando o motor principal ou o fallback usa Ollama, o Transass escolhe
automaticamente o melhor provedor disponível nesta ordem: NVIDIA via
`pynvml`/`nvidia-smi`, AMD Linux via `sysfs`/`rocm-smi` e, por fim, sensores do
sistema via `psutil`. No Windows, quando drivers não expõem uma API Python,
há uma tentativa opcional e tolerante de consultar zonas térmicas via
PowerShell/WMI. Se nenhum sensor estiver disponível, o guardião entra em modo
passivo, registra o motivo e libera a tradução.

O Transass não comanda a ventoinha: a curva de fan continua sob controle do
driver/firmware da GPU. A telemetria registra temperatura, sensor, RPM e
potência quando o fornecedor expõe esses dados. No caminho in-process V2.3.8,
o guardião também observa a temperatura antes de cada chamada ao modelo. Ao
atingir o limite configurado, a proteção preventiva do aplicativo pode ser
suspensa por até **60 segundos**, conforme a configuração legada atual, para
permitir a atuação do driver/firmware; se a temperatura persistir, a fila é
interrompida.

Para integrações novas, o módulo oferece um backoff com histerese:

```python
from gpu_thermal_guard import ThermalGuard

guard = ThermalGuard.create_auto()
for batch in subtitle_batches:
    guard.wait_if_hot()
    response = ollama_client.translate(batch)
```

O `ThermalGuard` padrão inicia o backoff a **95 °C**, só libera abaixo de
**85 °C**, considera **105 °C** crítico e espera no máximo **300 segundos**.
Uma falha ou ausência de sensor nunca interrompe o job.

O mecanismo não altera o modelo nem desativa o Ollama. Ele apenas evita que
uma tradução em lote mantenha a GPU aquecendo até o desligamento de proteção
do kernel. Se nenhum sensor compatível estiver disponível, o app informa isso
no log e mantém o comportamento normal. Os limites podem ser ajustados no
ambiente:

```env
TRANSASS_GPU_THERMAL_GUARD=1
TRANSASS_GPU_THERMAL_WARN_C=90
TRANSASS_GPU_THERMAL_STOP_C=100
TRANSASS_GPU_THERMAL_INTERVAL_S=1
# Leituras consecutivas acima do limite preventivo; padrão: 2.
TRANSASS_GPU_THERMAL_CONFIRMATIONS=2
# Janela de observação para a curva de fan; padrão: 30 segundos.
TRANSASS_GPU_THERMAL_COOLING_WINDOW_S=30
# Suspensão temporária da proteção preventiva após atingir o limite; padrão: 60s.
TRANSASS_GPU_THERMAL_TRIP_OVERRIDE_S=60
# Ponto de retomada; vazio usa alerta - 5°C (90 -> 85°C).
TRANSASS_GPU_THERMAL_RESUME_C=
```

O limite preventivo continua exigindo duas leituras consecutivas e a janela
configurada, mas a carga deixa de receber novas chamadas já no alerta. Para
acompanhar também as mensagens do driver/kernel em Linux, use outra janela do
terminal:

```sh
watch -n 1 sensors
journalctl -kf | grep -Ei 'amdgpu|drm|gpu|thermal'
```

Esses comandos são observacionais: a proteção térmica real do hardware é
gerenciada pelo firmware/driver, não pelo Transass.

Depois de uma parada térmica, aguarde a GPU esfriar e inicie uma nova fila.
Também é recomendável manter o driver, ventilação e curva de fan em boas
condições: a proteção do aplicativo é uma camada preventiva, não substitui a
proteção térmica do firmware/kernel.

**Idioma de origem**: no mesmo diálogo ⚙ Motor, o campo **"Idioma de origem da
legenda"** define o idioma padrão da legenda fonte (destino sempre português do
Brasil). Padrão: `inglês`. Exemplos válidos: `espanhol`, `japonês`, `francês`,
`coreano`. Karaokê e signs/songs são preservados automaticamente (não são
traduzidos). Também pode ser definido via variável de ambiente
`TRANSLATOR_SOURCE_LANGUAGE` no `.env`.

**Seleção por episódio na lista**: cada episódio na fila tem um seletor de
idioma de origem. Ao clicar nele, o app **descobre todos os idiomas** das
legendas daquele vídeo (sidecars como `ep01.esp.ass` e faixas internas do MKV)
e lista as opções; basta escolher qual traduzir. O valor escolhido é enviado no
`/start` e usado tanto na resolução da fonte quanto no prompt do modelo.

**Seleção por temporada (mais rápida)**: como temporadas vêm de uma fonte única
com legendas no mesmo idioma, use o seletor **"Idioma da temporada"** acima da
lista. O botão **"Detectar"** inspeciona o primeiro episódio catalogado e
popula os idiomas disponíveis; ao escolher um, ele é aplicado a **todos** os
episódios da pasta de uma vez (sem precisar marcar um a um).

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

Ao iniciar diretamente pelo arquivo `app.py`, o Transass também lê o `.env`
local. `MEDIA_ROOT` e `STATE_DIR` são os caminhos do computador hospedeiro;
os valores internos `/shows` e `/app/state` usados pelo Compose não são usados
fora do container. Assim, uma biblioteca como `/Tank/data/Shows` continua
visível no modo local e a configuração do motor é lida do mesmo `STATE_DIR`.

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
docker compose --env-file .env -f deploy/compose.yaml down
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
