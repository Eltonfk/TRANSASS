# AUTO-03D-TRACK2-LIVE-CAPTURED-EXECUTION-R1 — Contrato do Executor de Captura Live

Status: **DESIGN (não implementado)**. Este documento é a especificação do executor
de captura live do Track 2. Nenhuma execução, model call, transporte ou escrita
canônica é realizada por este documento.

## 1. Objetivo

Capturar a saída da retranslação web V2.3.8 **ao vivo** para um alvo
(subtítulo/episódio) definido, preservando as correções de ASS/`\N`/espaços
(commit `0c1ccdf`), e gravar o resultado como artefato de captura.

**Fora de escopo (exige gates separados):**
- Publicação/ingestão na Library.
- Mutação do estado canônico (PROJECT_STATE/HANDOFF) — requer transição documental à parte.
- Restart/deploy de produção.
- Protocolos B5/B6/B7 (são de batch/recovery, não captura live).

## 2. O que o executor faz (passo a passo)

1. Conecta ao endpoint web V2.3.8 de retranslação (já conectado: `WEB_PATH_CONNECTED`).
2. Alimenta o alvo (source subtitle/episode) pelo pipeline de retranslação V2.3.8
   (o mesmo corrigido em `0c1ccdf`, que preserva tags ASS, `\N` e espaços).
3. Captura a saída retranslatada final (`.ass` ou equivalente) e grava em
   `CAPTURE_OUTPUT_PATH` (arquivo novo, timestamped — nunca sobrescreve).
4. Encerra (forced stop). Nenhum loop, lote ou retry.

## 3. Bindings obrigatórios (todos devem estar definidos antes da execução)

| Binding | Valor | Estado |
|---|---|---|
| `TARGET_SUBTITLE_OR_EPISODE` | arquivo/episódio alvo da captura | **UNKNOWN** (definir) |
| `WEB_PATH` | endpoint web V2.3.8 retranslation | conectado (`WEB_PATH_CONNECTED`) |
| `MODEL` | `qwen3.5:9b` (Ollama) | definido |
| `CAPTURE_OUTPUT_PATH` | onde gravar a captura | **UNKNOWN** (definir) |
| `TRANSPORT_GUARD` | `DURABLE_EXCLUSIVE_TRANSPORT_CLAIM` | `max_client_calls=1`, `max_http_posts=1`, `max_retries=0` |
| `ACCOUNTING` | `model_calls` esperado = 1 (ou N do alvo), `retries=0` | estável |

## 4. Backup / Rollback

- **Backup:** a captura grava em arquivo novo timestamped; não há sobrescrita de
  artefato existente. Nenhuma ação destrutiva em canonical/Library/produção.
- **Rollback:** exclusão do arquivo de captura gerado (é aditivo; não muta
  canonical). Canonical/Library permanecem intactos, logo não exigem rollback.
- **Durability:** usar `src/subtranslate/v238_per_call_durability.py` para a
  claim de transporte exclusiva (consistente com B4/B5).

## 5. Proibido

- B5, B6, B7 (protocolos distintos).
- Retry (`max_retries=0`).
- Ingestão na Library (gate separado).
- Mutação de estado canônico (transição documental à parte).
- Restart/deploy de produção.
- Model call/transporte fora do `TRANSPORT_GUARD`.

## 6. HUMAN_GATE

```
HUMAN_GATE=AUTO-03D-TRACK2-LIVE-CAPTURED-EXECUTION-R1
AUTORIZACAO_NECESSARIA: AUTORIZAR (token literal, única mensagem)
PRECONDICOES (todas antes do token):
  - candidata git limpa (PROBE_EXIT_CODE=0)
  - TARGET_SUBTITLE_OR_EPISODE definido
  - CAPTURE_OUTPUT_PATH definido
  - AUDIT PASS (snapshot-first)
  - fresh probe pós-token com exit 0
AÇÃO NO AUTORIZAR:
  - executar exatamente 1 captura (1 Client.call / max 1 POST / 0 retry)
  - gravar captura em CAPTURE_OUTPUT_PATH
  - forced stop
POS-EXECUCAO:
  - auditoria pos-execução
  - gate de reconciliação canônica separado para registrar o resultado da captura
    (se desejado), via subtranslate-doc-sync / transição documental própria
```

## 7. Executor (toolchain) — a materializar

Este contrato é o **design**. O executor real (ex.:
`.opencode/tools/subtranslate_track2_live_captured_executor.py`) **não está
implementado**. Materializá-lo é um passo `TOOLCHAIN_CANDIDATA` separado:

1. Design do executor + testes offline deterministas.
2. Reconciliação (commit) na candidata.
3. Só então o `HUMAN_GATE=AUTO-03D-TRACK2-LIVE-CAPTURED-EXECUTION-R1` pode ser
   autorizado para execução real.

Até o executor ser materializado, o state machine `subtranslate-next` só pode
chegar a `SAFE_PLAN_TRACK2_LIVE_CAPTURED_PREFLIGHT_READ_ONLY` (READY, sem
execução) — é o ponto em que estamos agora.
