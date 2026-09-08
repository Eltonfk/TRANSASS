# Contratos canônicos V2.3.8

Este documento descreve o estado implementado. Planos anteriores estão em
`docs/archive/`; verbos no futuro foram aposentados junto com seus TODOs.

## Autoridades

| Responsabilidade | Autoridade |
|---|---|
| plano e estágios | `pipeline_registry.py` |
| execução e orçamento | `pipeline_orchestrator.py` |
| contexto web | `web_execution_context.py` |
| provider durável | `web_durable_provider.py` |
| chamadas físicas | `v238_llama_policy.py` |
| resposta e captura | `v238_response_provider.py` |
| lineage | `pipeline_lineage.py` |

## Contrato de execução

Uma operação live possui `operation_id`, provider/modelo autorizados,
`model_digest`, diretórios de captura/checkpoint e um único orçamento físico.
Cada chamada reserva orçamento antes do transporte e registra identidade de
tentativa. Retry não ganha carteira nova só porque pediu com educação.

## Contrato estrutural

O modelo produz conteúdo linguístico. Tempos, estilos, camadas, tags, desenhos
e quebras pertencem à fonte e são reconstruídos deterministicamente. Cobertura
incompleta, divergência estrutural ou resposta vazia invalidam o estágio.

## Contrato de karaokê

O V230 recebe fonte original e candidato V238. Descoberta usa estilo, conteúdo,
`name`, `effect` e flag de comentário. Linhas já traduzidas são preservadas;
somente resíduos ingleses recebem chamada com contexto lírico. Créditos,
elenco, títulos e notas técnicas são `SONG_NON_LYRIC`.

## Contrato de publicação

Somente resultado integralmente validado chega ao acervo/publicação. O resumo
público usa nomes seguros, enquanto caminhos internos e ledgers completos
permanecem no estado operacional. Toda aresta de lineage respeita o episódio.

## Contrato térmico

O guardião consulta sensores antes de novas chamadas ao Ollama. Alertas geram
backoff e diagnóstico; ausência de sensor entra em modo passivo. O software não
controla ventoinhas e não substitui limites de firmware/driver.
