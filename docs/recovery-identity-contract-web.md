# Contrato de Identidade de Recovery — Web Layer

**Versão**: 1.0 — design proposal  
**Data**: 2026-08-27  
**Status**: DRAFT — não é contrato canônico aprovado e não autoriza execução

## 1. Objetivo

Definir a identidade mínima e durável de um job web para que um restart possa:

- provar qual operação estava em andamento;
- preservar source, versão anterior e lineage;
- distinguir jobs diferentes para o mesmo episódio;
- detectar publicação parcial;
- impedir retry automático ambíguo ou duplicado.

Este documento descreve o contrato da camada web. Ele não altera
`PROJECT_STATE.json`, `HANDOFF_CHATGPT.md`, Library ou state real.

## 2. Lacuna Atual

O job web atual persiste alguns campos operacionais:

- `id` — identificador UUID do job;
- `session_id` — agrupador da fila;
- `episode_id`, quando disponível;
- `source_record_id` e `old_record_id` em retraduções;
- `source_abs`, `source`, status e timestamps.

Ainda faltam, como identidade explícita e validada:

- `operation_id` estável por operação;
- versão do esquema de identidade;
- hash da fonte efetivamente consumida;
- identidade do pipeline/stage;
- fingerprint não secreto do provider/modelo;
- destino e estado de publicação associados à operação;
- vínculo explícito entre identidade do job e lineage esperado.

## 3. Envelope Proposto

Cada job persistido deverá conter um objeto `recovery_identity` imutável:

```json
{
  "schema": "web-recovery-identity-v1",
  "job_id": "<uuid do job>",
  "operation_id": "<UUID da operação; estável nos attempts desta operação>",
  "session_id": "<uuid da fila>",
  "episode_id": 85,
  "series_id": 1,
  "source_record_id": 151,
  "old_record_id": 153,
  "source_sha256": "<sha256 dos bytes exatos da fonte consumida>",
  "source_kind": "LIBRARY",
  "pipeline_id": "<binding do plano aprovado>",
  "stage_id": "<binding do stage aprovado>",
  "provider_id": "<metadata não secreta do provider>",
  "model_id": "<metadata não secreta do modelo>",
  "model_digest": "<digest recebido do binding aprovado>",
  "family_contract_sha256": "<hash do family contract recebido do runtime>",
  "durable_call_root": "<raiz durável do runtime>",
  "episode_budget_ledger_path": "<ledger durável do runtime>",
  "destination_relpath": "<destino relativo seguro>",
  "lineage_contract": "<contrato de lineage aprovado>"
}
```

Os valores entre `<...>` são placeholders do contrato, não valores a serem
fabricados pela web. Os valores `v2_3_0`, `FULL_TRANSLATION_V226`,
`gemini-3.6-flash` e similares só podem aparecer como metadata observada de
uma execução; neste documento não são valores canônicos padrão.

### 3.1 Regras dos campos

| Campo | Regra |
|---|---|
| `schema` | Constante; mudança exige nova versão e review |
| `job_id` | UUID criado uma vez; nunca reutilizado |
| `operation_id` | UUID próprio; estável em todos os calls/attempts da operação; nunca reutilizado |
| `session_id` | Agrupa jobs, mas nunca substitui `operation_id` |
| `episode_id`/`series_id` | Identificam o episódio Library, não o arquivo sozinho |
| `source_record_id` | Registro efetivamente consumido |
| `old_record_id` | Versão que a retradução substitui logicamente; pode ser `null` na tradução inicial |
| `source_sha256` | SHA-256 dos bytes exatos do arquivo fonte materializado, antes do primeiro call |
| `pipeline_id`/`stage_id` | Identidade recebida do binding aprovado; a web não escolhe um valor canônico arbitrário |
| `model_digest` | Obrigatório antes de `LIVE_CAPTURED`; recebido do binding aprovado |
| `family_contract_sha256` | Hash do family contract produzido pelo runtime; não recalcular parcialmente na web |
| `durable_call_root`/`episode_budget_ledger_path` | Raízes duráveis devolvidas pelo runtime; obrigatórias antes de call live |
| `provider_id`/`model_id` | Metadata sem segredo; não usar API key como identidade |
| `destination_relpath` | Caminho relativo validado; nunca caminho absoluto vindo do cliente |
| `lineage_contract` | Versão explícita do contrato que materializará a saída |

## 4. Imutabilidade e Momento de Criação

1. `job_id`, `operation_id`, episódio, source records e destino são definidos
   antes de o job entrar em `WAITING`.
2. `source_sha256` é calculado sobre os bytes exatos da fonte materializada
   antes de qualquer chamada de modelo.
3. A intenção é persistida duravelmente em `WAITING`; o binding de execução
   (`model_digest`, family contract, call root e budget ledger) deve estar
   completo antes de `STARTING`/qualquer call live.
4. Após cada fase persistida, a identidade não pode ser alterada. Uma nova
   execução de `retry_failed` cria novo `job_id` e novo `operation_id`, com
   `parent_operation_id` apenas como referência histórica explícita.
5. Attempts físicos e retries do runtime ficam subordinados ao mesmo
   `operation_id`; não se cria novo operation_id para cada retry interno.
6. Provider/modelo não podem ser trocados silenciosamente durante a operação.

## 5. Estados e Recovery

| Estado persistido | Ação após restart |
|---|---|
| `WAITING` | Pode permanecer aguardando somente se a fila não iniciou a operação |
| `STARTING` | Marcar `FAILED`, motivo `service_restarted`, sem retry automático |
| `TRANSLATING` | Marcar `FAILED`, motivo `service_restarted`, sem retry automático |
| `VALIDATING` | Marcar `FAILED`, motivo `service_restarted`, sem retry automático |
| `PUBLISHING` | Marcar `FAILED_REQUIRES_RECONCILIATION`; verificar destino por hash e lineage |
| `COMPLETED` | Não reexecutar |
| `FAILED` | Não reexecutar automaticamente; novo job exige decisão explícita |

### 5.1 Caso `PUBLISHING`

Se o restart encontrar `PUBLISHING`:

1. localizar o destino somente pelo `destination_relpath` validado;
2. localizar o candidato/stage somente pela raiz durável vinculada ao
   `operation_id`;
3. calcular `candidate_sha256` e `published_sha256` usando bytes exatos;
4. consultar, sem criar novos registros, os `stage_record_id` e
   `final_record_id` associados ao `job_id`/operation;
5. comparar hashes, IDs, source e lineage com o envelope imutável;
6. não sobrescrever, não criar nova Library record e não executar retry;
7. produzir o diagnóstico web-local `publishing_reconciliation_required`;
8. permitir fechamento somente por fluxo explícito de reconciliação.

O envelope deve acrescentar, quando conhecidos, estes fatos de publicação:

```json
{
  "candidate_sha256": "<hash do candidato antes da publicação>",
  "published_sha256": "<hash do destino após publicação>",
  "stage_record_id": "<record criado pelo stage, se aplicável>",
  "final_record_id": "<record final, se aplicável>",
  "publication_marker": "<marcador durável associado à operação>"
}
```

## 6. Invariantes de Recovery

```text
I1: um operation_id nunca corresponde a dois episódios;
I2: dois operation_id nunca compartilham o mesmo job_id;
I3: source_record_id + source_sha256 identificam os bytes da fonte consumida;
I4: nenhuma chamada de modelo live ocorre sem binding de identidade completa;
I5: attempts físicos e retries internos compartilham operation_id e têm IDs próprios;
I6: restart nunca transforma estado desconhecido em retry automático;
I7: PUBLISHING não é tratado como COMPLETED sem evidência de publicação;
I8: nenhum job FAILED ou com reconciliação pendente é automaticamente reenfileirado;
I9: lineage da saída só pode usar os record IDs do envelope imutável;
I10: um claim concorrente exige token/fencing verificável e transição CAS durável;
I11: um call ledger rejeita o mesmo physical_attempt_id com payload/hash divergente;
I12: retry_failed cria nova operação explicitamente ligada à anterior, nunca reutiliza sua identidade.
```

## 7. Binding, Claims e Call Ledger

Antes de `STARTING`, o web layer deve obter do runtime o binding aprovado que
preenche `model_digest`, `family_contract_sha256`, `durable_call_root` e
`episode_budget_ledger_path`. Se qualquer um faltar, a operação permanece sem
call e falha fechada.

Cada attempt do runtime deve usar as primitivas já existentes em
`v238_per_call_durability.py`:

- `logical_batch_id` para a unidade lógica;
- `physical_attempt_id` para o attempt físico;
- `parent_attempt_id` obrigatório em `RETRY`;
- `request_payload_sha256` para detectar payload divergente;
- `episode_family_id` e `family_contract_sha256` para ownership;
- `EpisodeBudgetLedger` para claim, limite e consumo.

O web layer deve persistir apenas a referência/resultado desse ledger e não
duplicar sua semântica. Um restart não pode recriar claims: deve ler o estado
durável e encaminhar qualquer estado ambíguo para reconciliação.

### 7.1 Concorrência

O job deve ser reivindicado sob o lock de estado existente, com uma transição
compare-and-set (CAS) baseada no `job_id`, `operation_id` e status esperado.
Um segundo worker que observe status diferente deve sair sem executar call ou
publicação. O lock do web não substitui o lock do ledger canônico; ambos são
necessários quando o runtime V2.3.8 estiver conectado.

## 8. Compatibilidade com V2.3.8

O envelope web deverá mapear, sem substituir, os campos exigidos pelo runtime
V2.3.8:

| Web | Runtime V2.3.8 |
|---|---|
| `operation_id` | `execution_context.operation_id` |
| `episode_id` | `execution_context.episode_id` |
| `source_sha256` | `execution_context.source_sha256` |
| `provider_id`/`model_id` | `model`/`model_digest` após binding aprovado |
| `pipeline_id`/`stage_id` | `pipeline_id`/`stage_id` |
| `source_record_id`/`old_record_id` | lineage e materialização web |

O web layer não deve fabricar `model_digest`, `family_id` ou outros campos
canônicos. Esses valores precisam vir do binding aprovado do runtime V2.3.8.

## 9. Plano de Implementação Posterior

1. Adicionar construtor puro `build_recovery_identity(job, source, config)`;
2. adicionar validação fail-closed do envelope;
3. persistir o envelope junto com o job inicial;
4. incluir o envelope na auditoria e no diagnóstico de falhas;
5. atualizar `_load_state` para distinguir `PUBLISHING_RECONCILIATION_REQUIRED`;
6. adicionar testes offline para duplicidade, mismatch de hash, restart e
   publicação parcial;
7. revisar com `subtranslate-review` antes de qualquer deploy.

## 10. Testes Obrigatórios

- duas operações para o mesmo episódio têm `operation_id` distintos;
- mesmo `operation_id` não pode ser associado a outro episódio/source;
- source hash alterado entre preflight e execução bloqueia o job;
- job `PUBLISHING` após restart não executa retry;
- destino existente com hash divergente exige reconciliação;
- campos canônicos ausentes bloqueiam a execução;
- payload divergente para o mesmo `physical_attempt_id` bloqueia o call;
- claim concorrente/CAS vencido não executa segundo worker;
- envelope não contém API keys ou caminhos absolutos expostos na API pública;
- migração de jobs antigos sem envelope fica como `LEGACY_IDENTITY_REVIEW_REQUIRED`,
  nunca como operação automaticamente retomável.

## 11. Gates

- **Design**: autorizado nesta sessão;
- **Implementação**: gate separado após review do design;
- **State real/Library**: gate operacional separado;
- **Deploy**: gate de produção separado;
- **Reconciliação canônica**: `subtranslate-canonical-reconciliation` +
  autorização documental separada.

**Conclusão**: o design fecha a lacuna conceitual identificada pela revisão,
mas não declara a implementação atual como recovery-canonical nem autoriza
deploy.
