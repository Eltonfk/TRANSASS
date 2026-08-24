---
name: subtranslate-canonical-reconciliation
description: Procedimento especializado para reconciliar runtime truth com PROJECT_STATE/HANDOFF do Subtranslate - detecta stale canonical state, preserva fotografias historicas, usa objetos aditivos, dry-run obrigatorio e exige autorizacao separada para escrita documental.
---

# subtranslate-canonical-reconciliation

## Principios

1. AGENTS.md ja foi carregado e permanece soberano. Esta skill nao o substitui.
2. Esta skill nao concede autorizacao operacional.
3. Carregar esta skill nunca autoriza side effects.
4. Trabalhe fail-closed: em caso de duvida material, BLOCK.
5. Runtime truth comprovado e autorizacao documental sao dimensoes separadas.

SKILL_INSTRUCTION_IS_NOT_AUTHORIZATION = true

Carregar esta skill nunca autoriza por si so:

- PROJECT_STATE write;
- HANDOFF write;
- manifest write;
- canonical reconciliation apply;
- runtime evidence mutation;
- model call;
- transport;
- retry;
- batch execution;
- Library write;
- production change;
- main change;
- code change;
- commit;
- push;
- destructive cleanup.

A skill descreve o PROCEDIMENTO. Escrita documental continua exigindo
autorizacao explicita separada da sessao corrente.

## Papeis

- agent = quem executa;
- command = entry point / workflow conveniente;
- skill = procedimento especializado reutilizavel;
- reconciliation executor = mecanismo concreto de escrita documental que
  pode atuar somente apos autorizacao explicita.

Esta skill nao substitui AGENTS.md, `subtranslate-evidence-audit`,
`subtranslate-doc-sync` nem `subtranslate-build`.

## Proposito

Reconciliar, de maneira fail-closed, a diferenca entre:

RUNTIME TRUTH

e

CANONICAL DOCUMENTATION

quando runtime evidence prova fatos posteriores ao estado canonico.

Alvos documentais principais:

PROJECT_STATE.json
HANDOFF_CHATGPT.md

Nunca "corrigir" runtime evidence para faze-lo concordar com documentacao.

A direcao normal da reconciliacao e:

runtime fact comprovado
-> documentacao canonica atualizada

nunca o inverso.

## Modos

MODE = RECONCILIATION_PREFLIGHT_READ_ONLY
MODE = RECONCILIATION_PLAN
MODE = DOCUMENTAL_APPLY
MODE = POST_WRITE_VERIFICATION

Inferir o modo pelo pedido do usuario e pelo estado corrente.

DOCUMENTAL_APPLY so e permitido quando:

1. a reconciliacao necessaria foi provada;
2. o plano exato de mudancas foi determinado;
3. hashes/preconditions atuais convergem;
4. existe autorizacao explicita da sessao corrente para escrever.

Carregar a skill nao satisfaz o item 4.

## Authority / inputs

Respeitar AGENTS.md como soberano.

Quando relevante, ler:

- PROJECT_STATE.json;
- HANDOFF_CHATGPT.md;
- runtime evidence;
- operation.json;
- episode-budget.json;
- call state/capture_state;
- request/response metadata;
- derived evidence;
- checkpoints;
- Git candidate identity;
- protected surface status.

Pode usar `subtranslate-evidence-audit` para auditoria profunda.
Nao duplicar integralmente essa skill.

## Stale state detection

Detectar quando:

runtime evidence > canonical documentation

e retornar:

STALE_CANONICAL_STATE = YES
CANONICAL_STATE_RECONCILIATION_REQUIRED = YES

Enquanto isso, novos side effects permanecem bloqueados conforme AGENTS.md.

Nao considerar silenciosamente stale-state "aceitavel".

## Pre-execution snapshot immutability

Registros de autorizacao pre-execucao sao fotografia historica.

Exemplo conceitual:

authorization_status = AUTHORIZED_NOT_EXECUTED
execution_executed = false

Se posteriormente a execucao aconteceu, NAO reescrever retrospectivamente
esse objeto para "executed=true".

Preferir:

pre-execution authorization object (preservado)
+
novo execution/factual object aditivo

Esse padrao deve ser generico para qualquer canario/gate/reconciliacao.

## Additive history

Correcoes e fatos posteriores devem preferir:

NEW_ADDITIVE_OBJECT

em vez de reescrever historia.

O novo objeto deve registrar, quando aplicavel:

- scope;
- factual execution status;
- logical/physical IDs;
- batch/unit identity;
- policy;
- request/response hashes;
- durable final state;
- accounting pos-execucao;
- retry/transport counts;
- protected-surface flags;
- backup/manifest reference;
- future authorization flags.

Nao exigir campos irrelevantes em todos os casos.

## State / status / next_action

Determinar, nunca inventar cegamente:

current_operation
state
status
latest_decision
next_action

Regra:

KEEP quando o fato novo nao exige mudanca.
UPDATE somente quando runtime/evidence prova mudanca semantica.

Nao declarar:

- E07 COMPLETE;
- missao COMPLETE;
- proximo batch autorizado;
- blocker resolvido;

sem evidencia explicita.

Quando houver duvida sobre nomenclatura/padrao: BLOCK.

## Accounting reconciliation

Quando runtime avancou, auditar e reconciliar os campos canonicos
relevantes, quando aplicavel:

- global historical transports;
- current mission model calls;
- operation/family transports;
- retries;
- outros contadores realmente existentes.

Comparar:

CANONICAL_BEFORE
RUNTIME_FACT
CANONICAL_TARGET

Nao alterar contador so porque "parece provavel".
Cada incremento precisa de origem factual.

## HANDOFF append-only

HANDOFF_CHATGPT.md e historico append-only.

A reconciliacao deve:

- preservar prefixo existente byte-exato quando possivel;
- adicionar novo addendum;
- nao remover historico;
- nao reescrever decisoes antigas;
- registrar o fato novo;
- registrar limites ainda vigentes;
- registrar proximos gates.

Se correcao de erro historico for necessaria: usar correcao aditiva
explicita, preservando a evidencia original.

## PROJECT_STATE

PROJECT_STATE.json pode receber:

- atualizacao de ponteiros atuais;
- accounting factual;
- novo objeto aditivo;
- flags atuais comprovadas.

Deve preservar:

- fotografias pre-execucao;
- objetos historicos;
- fatos anteriores ainda validos;
- schema tolerado pelos consumers.

Antes da escrita:

PROJECT_STATE_SCHEMA_COMPATIBILITY = PROVEN

ou BLOCK.

## Dry-run obrigatorio

Antes de qualquer DOCUMENTAL_APPLY:

construir candidatos integralmente em memoria.

Retornar:

STATE_BEFORE_SHA256
STATE_CANDIDATE_SHA256
HANDOFF_BEFORE_SHA256
HANDOFF_CANDIDATE_SHA256
CHANGED_TOP_LEVEL_KEYS = [...]
HANDOFF_APPEND_ONLY = YES/NO
OTHER_FILES_EXPECTED_TO_CHANGE = [...]

Se mudancas excederem o plano: BLOCK.

## Backup

Antes de escrita autorizada:

- backup de PROJECT_STATE.json;
- backup de HANDOFF_CHATGPT.md;
- quando util, manifest do plano.

Backup NAO concede liberdade para reescrever runtime evidence.
Nao usar backup para apagar verdade operacional.

## Atomic write / verify

Quando DOCUMENTAL_APPLY for autorizado:

- escrever de forma controlada/atomica;
- validar JSON;
- verificar hashes pos-write;
- verificar objeto aditivo;
- verificar campos KEEP;
- verificar changed-key allowlist;
- verificar HANDOFF append-only;
- verificar que nenhum outro arquivo mudou.

Falha pos-write: nao fazer correcoes silenciosas fora do plano.
Reportar FAIL/BLOCK conforme o caso.

## Protected surfaces

Reconciliacao documental nao autoriza:

- runtime evidence mutation;
- mission-summary rewrite;
- checkpoint rewrite;
- Library;
- production;
- main;
- candidate code;
- Git commit/push.

Qualquer alteracao nessas superficies: BLOCK.

## Future authorization

A reconciliacao deve registrar claramente quando:

future_side_effects_authorized = false

ou equivalente factual.

Reconciliacao passada NAO autoriza automaticamente proximo batch/gate.

Depois de reconciliar, novo USER_DECISION / USER_AUTHORIZATION_REQUIRED
pode continuar necessario.

## No retroactive authorization

Nunca interpretar execucao passada como "portanto estava autorizada".

Registrar fato operacional e autorizacao sao dimensoes separadas.

Se execucao ocorreu sem autorizacao registrada:

- preservar a verdade;
- registrar a irregularidade.

Nao criar autorizacao retroativa.

## PASS / FAIL / BLOCK

PASS = runtime truth comprovado e plano/aplicacao documental consistente.
FAIL = uma verificacao concreta falhou.
BLOCK = nao e seguro reconciliar/aplicar, ou dados materiais permanecem
UNKNOWN.

Ausencia de erro != PASS.

## Output - preflight

Quando read-only:

SUBTRANSLATE_CANONICAL_RECONCILIATION = PASS/FAIL/BLOCK

MODE = RECONCILIATION_PREFLIGHT_READ_ONLY

STALE_CANONICAL_STATE = YES/NO
CANONICAL_STATE_RECONCILIATION_REQUIRED = YES/NO

CURRENT_OPERATION_ACTION = KEEP/UPDATE
CURRENT_OPERATION_VALUE = ...
STATE_ACTION = KEEP/UPDATE
STATE_VALUE = ...
STATUS_ACTION = KEEP/UPDATE
STATUS_VALUE = ...
LATEST_DECISION_ACTION = KEEP/UPDATE
LATEST_DECISION_VALUE = ...
NEXT_ACTION_ACTION = KEEP/UPDATE
NEXT_ACTION_VALUE = ...

ACCOUNTING_CHANGES = [...]
HISTORICAL_RECORDS_TO_KEEP = [...]
NEW_ADDITIVE_OBJECT = ...
HANDOFF_APPEND_REQUIRED = YES/NO
PROJECT_STATE_SCHEMA_COMPATIBILITY = PROVEN/UNKNOWN/RISK
DOCUMENTATION_WRITE_READY = YES/NO

SIDE_EFFECTS_EXECUTED = NO
FILES_WRITTEN = []
BLOCKERS = [...]

## Output - apply

Quando escrita explicitamente autorizada:

SUBTRANSLATE_CANONICAL_RECONCILIATION_APPLY = PASS/FAIL/BLOCK

STATE_BEFORE_SHA256 = ...
STATE_AFTER_SHA256 = ...
HANDOFF_BEFORE_SHA256 = ...
HANDOFF_AFTER_SHA256 = ...

CHANGED_TOP_LEVEL_KEYS = [...]
HISTORICAL_RECORDS_PRESERVED = YES/NO
NEW_ADDITIVE_OBJECT = ...
HANDOFF_APPEND_ONLY = YES/NO
OTHER_FILES_CHANGED = [...]
BACKUP_DIR = ...
COMMIT_CREATED = NO
FUTURE_SIDE_EFFECTS_AUTHORIZED = ...
NEXT_ACTION = ...

Nao force campos irrelevantes; adapte mantendo os principios.

## Relacao com evidence audit

Quando runtime facts precisarem ser comprovados profundamente, usar ou
carregar `subtranslate-evidence-audit` se apropriado.

Mas:

reconciliation skill = decide como reconciliar;
evidence skill = prova fatos/evidencias.

Nao misturar responsabilidades.

## Relacao com commands/agentes

- agent = quem executa;
- command = entry point / convenience workflow;
- skill = procedimento reutilizavel especializado;
- reconciliation executor = mecanismo concreto de escrita documental.

Esta skill nao substitui `preflight.md`, `substatus.md`, `subtranslate-evidence-audit`,
`subtranslate-doc-sync` nem `subtranslate-build`.
