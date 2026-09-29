# Configuração

A configuração pode vir da tela **Motor**, de `transport_config.json` no
diretório de estado ou do ambiente. Após o primeiro salvamento pela interface,
o arquivo persistido tem precedência. Segredos usam o cofre do sistema quando
disponível e nunca entram no contexto do pipeline.

## Caminhos

| Variável | Função |
|---|---|
| `MEDIA_ROOT` | Biblioteca de vídeos no host Docker/manual |
| `STATE_DIR` | Fila, acervo, configuração e evidência |
| `BIND_ADDR` / `WEB_PORT` | Endereço da interface Docker |
| `TRANSASS_DATA_DIR` | Raiz de dados do Desktop |
| `TRANSASS_CONFIG_DIR` | Configuração do Desktop |

No Compose, os caminhos internos são `/shows` e `/app/state`; não os copie
para uma execução Desktop. O launcher traduz esses caminhos sozinho — ele já
tem trabalho suficiente traduzindo legendas.

## Tradução

| Variável | Padrão |
|---|---|
| `TRANSLATOR_PIPELINE` | `v3` |
| `TRANSLATOR_SOURCE_LANGUAGE` | `inglês` |
| `TRANSLATOR_OLLAMA_MODEL` | `qwen3.5:9b` |
| `TRANSLATOR_OLLAMA_URL` | definido pela embalagem |
| `V238_QWEN_PHYSICAL_MAXIMUM` | `256` |

Providers suportados: `ollama`, `gemini`, `groq`, `deepseek`, `nvidia` e
`openai_compat`. O fallback é opcional e atua somente em falha de transporte;
um texto ruim não troca de fornecedor escondido atrás da cortina.

Para DeepSeek, use `deepseek-v4-flash` como opção recomendada ou
`deepseek-v4-pro` para casos mais exigentes. Configurações antigas com
`deepseek-chat` ou `deepseek-reasoner` são migradas automaticamente para
`deepseek-v4-flash` antes da chamada; o alias antigo fica apenas como
compatibilidade, não como modelo efetivamente enviado.

## Segurança dos providers

Gemini, Groq, NVIDIA e DeepSeek usam endpoints oficiais fixos. O Transass
recusa `base_url` personalizada para esses providers para que uma configuração
maliciosa não leve junto a API key. Ollama e `openai_compat` aceitam endpoints
locais/personalizados, com URL explícita e sem credenciais embutidas.

`TRANSASS_MAX_HTTP_RESPONSE_BYTES` limita o corpo recebido de um provider. O
padrão é `16777216` (16 MiB); respostas maiores falham fechadas.

## Thermal Guard

O guard do aplicativo verifica cancelamento/resfriamento antes de cada lote; a
telemetria contínua é registrada durante a tradução. O firmware continua sendo
responsável por controlar as ventoinhas. Os limites configuráveis incluem:

```env
TRANSASS_GPU_THERMAL_GUARD=1
TRANSASS_GPU_THERMAL_WARN_C=90
TRANSASS_GPU_THERMAL_STOP_C=100
TRANSASS_GPU_THERMAL_INTERVAL_S=1
TRANSASS_GPU_THERMAL_CONFIRMATIONS=2
TRANSASS_GPU_THERMAL_COOLING_WINDOW_S=30
TRANSASS_GPU_THERMAL_TRIP_OVERRIDE_S=60
TRANSASS_GPU_THERMAL_RESUME_C=
```

Em hosts NVIDIA com Docker, o Compose padrão não concede acesso à GPU. Para
habilitar somente a capacidade `utility` (NVML/`nvidia-smi`, sem CUDA compute),
use o override opcional `deploy/compose.nvidia.yaml`:

```sh
docker compose --env-file .env \
  -f deploy/compose.yaml \
  -f deploy/compose.nvidia.yaml up -d
```

O host precisa ter driver NVIDIA e NVIDIA Container Toolkit funcionais. Sem
esse override, ou sem sensores disponíveis, o guard mantém a degradação passiva.

O módulo agnóstico de integração usa histerese (`95/85/105 °C`) e timeout de
300 segundos. Ele detecta NVIDIA, AMD e Intel em Linux/Windows e entra em modo
passivo quando o sistema não fornece sensores. O Transass não controla
ventoinhas nem substitui firmware, driver ou limpeza física.

Consulte `.env.example` para a lista completa e valores documentados.
