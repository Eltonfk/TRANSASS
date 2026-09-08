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
| `TRANSLATOR_PIPELINE` | `v2_3_8` |
| `TRANSLATOR_SOURCE_LANGUAGE` | `inglês` |
| `TRANSLATOR_OLLAMA_MODEL` | `qwen3.5:9b` |
| `TRANSLATOR_OLLAMA_URL` | definido pela embalagem |
| `V238_QWEN_PHYSICAL_MAXIMUM` | `256` |

Providers suportados: `ollama`, `gemini`, `groq`, `deepseek`, `nvidia` e
`openai_compat`. O fallback é opcional e atua somente em falha de transporte;
um texto ruim não troca de fornecedor escondido atrás da cortina.

## Thermal Guard

O caminho V2.3.8 registra telemetria por segundo e pode suspender a proteção
preventiva inicial enquanto o firmware acelera as ventoinhas:

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

O módulo agnóstico para integrações novas usa histerese (`95/85/105 °C`) e
timeout de 300 segundos. Ele detecta NVIDIA, AMD e Intel em Linux/Windows e
entra em modo passivo quando o sistema não fornece sensores. O Transass não
controla ventoinhas nem substitui firmware, driver ou uma limpeza física que
está sendo adiada desde o último verão.

Consulte `.env.example` para a lista completa e valores documentados.
