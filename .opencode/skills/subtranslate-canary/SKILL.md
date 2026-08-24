---
name: subtranslate-canary
description: Procedimento especializado para canario isolado de batch do Subtranslate - preflight read-only, identidade de payload, 1 Client.call / max 1 POST / 0 retry, forced stop antes do proximo batch, auditoria pos-execucao e reconciliacao canonica. Nao autoriza execucao.
---

# subtranslate-canary

## Principios

1. AGENTS.md ja foi carregado e permanece soberano. Esta skill nao o substitui.
2. Esta skill nao concede autorizacao operacional.
3. Carregar esta skill nunca autoriza side effects.
4. Trabalhe fail-closed: em caso de duvida material, BLOCK.
5. Autorizacao documental historica nao equivale a autorizacao operacional
   da sessao corrente.

SKILL_INSTRUCTION_IS_NOT_AUTHORIZATION = true

Carregar esta skill nunca autoriza:

- model call;
- HTTP POST;
- transport;
- retry;
- reserve;
- batch execution;
- translation/retranslation;
- PROJECT_STATE/HANDOFF write;
- episode-budget mutation;
- runtime evidence mutation;
- Library write;
- production change;
- main change;
- code change;
- commit;
- push;
- destructive cleanup.

A existencia de autorizacao documental de canario no PROJECT_STATE tambem
NAO significa que esta sessao pode executar a call real.

## Papeis

- agent = quem executa;
- command = entry point / workflow conveniente;
- skill = procedimento reutilizavel especializado;
- executor = mecanismo operacional concreto (ex.: script externo) que pode
  produzir side effects somente apos autorizacao explicita da sessao.

Esta skill nao substitui AGENTS.md, `preflight.md`, `subtranslate-evidence-audit`,
`subtranslate-build` nem scripts executores externos.

## Ciclo do canario

CANONICAL STATE
↓
READ-ONLY PREFLIGHT
↓
IDENTIDADE DO BATCH
↓
PAYLOAD DETERMINISTICO
↓
ACCOUNTING / LEDGER CHECK
↓
PRE-EXEC DOCUMENTAL AUTHORIZATION
↓
EXPLICIT OPERATIONAL AUTHORIZATION
↓
EXACTLY ONE Client.call
↓
MAX 1 HTTP POST
↓
0 RETRIES
↓
FORCED STOP BEFORE NEXT BATCH
↓
EVIDENCE VERIFICATION
↓
CANONICAL RECONCILIATION REQUIRED
↓
NEW USER DECISION

A skill nunca pula gates.

## Modos

MODE = PREFLIGHT_READ_ONLY
MODE = PREEXEC_AUTHORIZATION_PLANNING
MODE = EXECUTION_PRECHECK
MODE = SINGLE_CALL_EXECUTION
MODE = POST_EXECUTION_AUDIT

Determine o modo a partir do pedido do usuario e do estado corrente.

SINGLE_CALL_EXECUTION so e permitido quando existe autorizacao explicita
da sessao corrente para a execucao operacional. Autorizacao historica ou
documental sozinha nao basta.

## Modelo de autorizacao

A = skill carregada
B = autorizacao documental pre-execucao existe
C = autorizacao operacional de execucao da sessao corrente existe

Somente A + B + C permite SINGLE_CALL_EXECUTION.

- A sozinho = nunca.
- A + B = ainda nao.

## Preflight read-only

No preflight, verificar quando aplicavel:

- workspace / branch / HEAD / tree;
- current canonical state;
- authorization records;
- operation/family;
- episode-budget;
- initial_consumed;
- retry_consumed;
- reservations;
- ausencia de evidence do batch futuro;
- unit IDs;
- logical_batch_id;
- unit_membership_sha256;
- source hash;
- model/config identity;
- starting normalization policy;
- payload canonical SHA256;
- predicted logical_call_id;
- predicted physical_attempt_id;
- Client.call unchanged;
- maximum HTTP posts;
- retry behavior;
- forced stop;
- side-effect allowlist.

Se qualquer claim material for UNKNOWN: BLOCK.

Para `B4_RECOVERY_CALL_PREFLIGHT_READ_ONLY`, a existencia da toolchain de
execucao e claim material do proprio preflight, nao detalhe adiavel. Exija
executor_id, toolchain fingerprint, modelo/digest e transport guard especificos
da chamada B4. A toolchain de `RECOVERY_LEDGER_REPREPARATION` nao serve.

Se a toolchain B4 estiver ausente, nao materializada, a definir futuramente ou
UNKNOWN, o unico resultado valido e:

```text
SUBTRANSLATE_CANARY_PREFLIGHT = BLOCK
B4_RECOVERY_CALL_PREFLIGHT_STATUS = BLOCKED
READY_FOR_DOCUMENTAL_AUTHORIZATION = NO
BLOCKERS = [B4_RECOVERY_CALL_EXECUTION_TOOLCHAIN_NOT_MATERIALIZED]
NEXT_GATE = AUTO-03D-B4-RECOVERY-CALL-TOOLCHAIN-DISCOVERY-R1
```

Nunca use PASS para significar apenas que a analise terminou. PASS/READY
significa que todo o contrato executavel futuro ja esta materializado e
vinculado, embora ainda nao autorizado.

## Policy / normalization

Nao escolher silenciosamente outra normalization policy.

Sempre:

- descobrir policy autorizada/canonica;
- verificar scope;
- impedir vazamento de override de outro batch;
- nao fazer fallback heuristico;
- nao trocar V3/V2 sem autorizacao documental especifica.

Se raw response for invalida e a policy autorizada nao cobrir:

STOP.

Nao retry. Nao escolher outra policy. Nao avancar proximo batch.

## Execution contract

Quando operacionalmente autorizada, exigir:

EXACTLY_ONE_CLIENT_CALL = true

MAX_NEW_INITIAL_CALLS = valor autorizado, normalmente 1
MAX_NEW_PHYSICAL_TRANSPORTS = valor autorizado, normalmente 1
MAX_NEW_RETRIES = valor autorizado, normalmente 0

O executor precisa ser:

- sem loop de batches;
- sem runner inline historico;
- sem while;
- sem retry automatico;
- sem auto-avancar.

Se Client.call puder realizar mais de 1 POST: BLOCK.

## Payload identity

Antes do transport:

- reconstruir payload deterministicamente;
- validar canonical SHA256 contra o valor autorizado/preflight;
- validar logical identity;
- validar physical attempt identity quando previsivel;
- validar model/config/source/family.

Payload divergente: BLOCK BEFORE NETWORK.

## Transport guard

Exigir mecanismo externo capaz de impedir um segundo POST. Nao depender
apenas de intencao.

Se uma segunda tentativa puder atingir a rede: BLOCK.

## Retry

Retry nunca e implicito.

Resposta invalida: STOP.
Timeout: STOP.
Unknown outcome: STOP.
HTTP error: STOP.
Exception: STOP.

Retry requer nova autorizacao especifica quando aplicavel.

## Evidence truth

Apos inicio da execucao real, nunca apagar evidence para fingir rollback.

Preservar quando aplicavel:

- reservation;
- state.json;
- capture_state.json;
- request payload/metadata;
- response.body;
- response metadata;
- episode-budget;
- parse failure;
- derived evidence;
- transport state.

Rollback pos-transport e documental/operacional, nunca reescrita da historia.

## Forced stop

Depois de qualquer resultado:

PARSED_VALID
DERIVED_PARSED_VALID
PARSED_INVALID
TRANSPORT_OUTCOME_UNKNOWN
TRANSPORT_FAILED_CONFIRMED
CANCELLED_CONFIRMED
exception

o processo deve terminar antes do proximo batch.

Retornar:

POST_CALL_FORCED_STOP_PROVEN = YES

ou BLOCK.

## Post-execution

Depois de uma execucao:

- verificar physical attempt;
- terminal state;
- hashes;
- coverage/found/issues;
- ledger;
- accounting factual;
- ausencia de next-batch evidence;
- protected surfaces;
- retries;
- numero de HTTP POSTs.

Nao atualizar PROJECT_STATE/HANDOFF nesta mesma skill automaticamente.

Se runtime avancou:

CANONICAL_STATE_RECONCILIATION_REQUIRED = YES

e novos side effects ficam bloqueados ate reconciliacao.

## Integracao com evidence skill

Quando apropriado, recomendar `subtranslate-evidence-audit` para validacoes
profundas pos-execucao.

Nao copiar integralmente a skill de evidence audit. A canary skill foca o
ciclo e os gates do canario.

## Side-effect allowlist

Exigir allowlist explicita por execucao.

Padrao:

PRE_TRANSPORT_MAY_CHANGE = [...]
POST_TRANSPORT_MAY_CHANGE = [...]
INVALID_ONLY = [...]
DERIVED_ONLY = [...]
PROTECTED = [...]

Qualquer arquivo fora da allowlist: BLOCK.

## Output contract

O output adapta-se ao modo.

Para PREFLIGHT:

SUBTRANSLATE_CANARY_PREFLIGHT = PASS/FAIL/BLOCK

MODE = PREFLIGHT_READ_ONLY

BATCH_SCOPE = ...
UNIT_IDS = ...
LOGICAL_BATCH_ID = ...
UNIT_MEMBERSHIP_SHA256 = ...

STARTING_POLICY = ...

EXPECTED_REQUEST_PAYLOAD_SHA256 = ...
EXPECTED_LOGICAL_CALL_ID = ...
EXPECTED_PHYSICAL_ATTEMPT_ID = ...

CURRENT_INITIAL_CONSUMED = ...
CURRENT_RETRY_CONSUMED = ...
CURRENT_RESERVATIONS = ...

MAX_NEW_INITIAL_CALLS = ...
MAX_NEW_PHYSICAL_TRANSPORTS = ...
MAX_NEW_RETRIES = ...

ONE_CLIENT_CALL_MAX_HTTP_POSTS = ...
INTERNAL_HTTP_RETRY = YES/NO
NEXT_BATCH_CAN_START = YES/NO

READY_FOR_DOCUMENTAL_AUTHORIZATION = YES/NO

Para B4 recovery call, `YES` e proibido se executor_id, toolchain fingerprint,
modelo/digest ou transport guard especificos da chamada estiverem ausentes ou
UNKNOWN.

SIDE_EFFECTS_EXECUTED = NO
FILES_WRITTEN = []

BLOCKERS = [...]

Para pos-execucao:

SUBTRANSLATE_CANARY_RESULT = PASS/FAIL/BLOCK

MODE = POST_EXECUTION_AUDIT

PHYSICAL_ATTEMPT_ID = ...
FINAL_DURABLE_STATE = ...

HTTP_POST_COUNT = ...
RETRIES_EXECUTED = ...

FOUND_IDS = ...
ISSUES = ...

ACCOUNTING_RUNTIME_AFTER = ...

NEXT_BATCH_STARTED = YES/NO

CANONICAL_STATE_RECONCILIATION_REQUIRED = YES/NO

BLOCKERS = [...]

Nao force campos irrelevantes; adapte mantendo os principios.

## Relacao com commands/agentes

- agent = quem executa;
- command = entry point / convenience workflow;
- skill = procedimento reutilizavel especializado;
- executor = mecanismo operacional concreto.

Esta skill nao substitui `preflight.md`, `substatus.md`, `subtranslate-evidence-audit`,
`subtranslate-build` nem scripts executores externos.
