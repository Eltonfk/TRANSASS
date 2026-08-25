---
name: subtranslate-evidence-audit
description: Audita a consistencia entre estado canonico, HANDOFF, Git da candidata e runtime evidence do Subtranslate, estritamente read-only, antes de declarar PASS, apos execucao externa ou antes de reconciliacao canonica.
---

# subtranslate-evidence-audit

## Principios

1. AGENTS.md ja foi carregado e permanece soberano. Esta skill nao o substitui.
2. Esta skill nao concede autorizacao operacional.
3. Carregar esta skill nunca autoriza side effects.
4. Trabalhe fail-closed: em caso de duvida material, BLOCK.

SKILL_INSTRUCTION_IS_NOT_AUTHORIZATION = true

Carregar esta skill nunca autoriza:

- model calls;
- HTTP POST;
- transport;
- retry;
- batch execution;
- translation/retranslation;
- PROJECT_STATE/HANDOFF writes;
- Library writes;
- production changes;
- main changes;
- code changes;
- commit;
- push;
- destructive cleanup.

## Proposito

Auditar, de forma estritamente read-only, a consistencia entre:

- estado canonico (PROJECT_STATE.json);
- HANDOFF_CHATGPT.md;
- Git da candidata;
- runtime evidence (operation, ledger, reservations, calls, checkpoints);
- accounting;
- producao quando relevante.

Apropriada para: antes de declarar PASS; depois de execucao externa; quando
runtime evidence pode estar a frente do estado canonico; antes de canonical
reconciliation; investigacao de divergencia; validacao pos-gate.

## Autoridade

Respeite a hierarquia definida em AGENTS.md. Nao invente hierarquia
conflitante.

Leia o estado necessario para a tarefa, incluindo quando relevante:

- `@authority/PROJECT_STATE.json`;
- `@authority/HANDOFF_CHATGPT.md`;
- git HEAD/tree/status da candidata;
- operation.json;
- episode-budget.json;
- mission-summary.json;
- checkpoints;
- calls/*/state.json e calls/*/capture_state.json;
- request/response metadata;
- derived evidence;
- rollout/evidence preservado.

Nao obrigue leitura de todos os arquivos quando a questao puder ser
respondida com um subconjunto comprovadamente suficiente.

## Read-only absoluto

ALLOWED_SIDE_EFFECTS = NONE

O procedimento de auditoria nao pode: editar, criar, apagar, mover,
renomear, corrigir, normalizar, reconciliar, reservar attempt, iniciar
transport, chamar modelo, executar batch, alterar Git, tocar producao.

Auditor encontrou problema: REPORTA. Auditor NAO corrige silenciosamente.

## Procedimento

### A. Scope

- identificar exatamente o que esta sendo auditado;
- declarar read-only;
- identificar claims a provar.

### B. Workspace identity

- pwd/root;
- branch;
- HEAD;
- tree;
- status/diff relevante.

### C. Canonical state

- current_operation;
- state;
- status;
- next_action;
- latest_decision;
- accounting relevante;
- authorizations relevantes.

### D. Runtime evidence

- operation/family;
- ledger;
- initial/retry;
- reservations;
- call states;
- request/response hashes;
- terminal states;
- checkpoints;
- absence/presence assertions.

### E. Cross-check

- canonical accounting vs factual runtime;
- authorization scope vs observed side effects;
- candidate identity vs evidence identity;
- batch progression;
- exactly-once invariants;
- evidence chronology.

### F. Protected surfaces (quando relevante)

- main;
- producao;
- Library;
- evidencia historica.

### G. PASS criteria

- claims comprovados;
- hashes/identity convergem;
- sem evidence inesperada;
- sem stale-state material nao registrado;
- distincao testado vs nao testado.

### H. Result

- PASS / FAIL / BLOCK.

## Stale canonical state

Se runtime evidence comprovar fatos posteriores ao PROJECT_STATE/HANDOFF:
nao tente interpretar isso como normal. Retorne claramente

CANONICAL_STATE_RECONCILIATION_REQUIRED

e BLOCK para novos side effects conforme AGENTS.md.

Distinguir runtime truth vs canonical documentation sem apagar nenhum dos
dois.

## Accounting

Quando accounting fizer parte do escopo, audite pelo menos quando aplicavel:

- global historical transports;
- current mission model calls;
- operation/family transports;
- retries;
- initial_consumed;
- retry_consumed;
- reservations;
- successful durable responses;
- invalid responses;
- unknown outcomes;
- confirmed failures/cancellations.

Nao assuma nomes de campos quando a versao/evidence real puder demonstra-los.
Informe a origem de cada numero importante.

## Exactly-once / lineage

Verifique quando relevante:

- logical batch identity;
- logical call identity;
- physical attempt identity;
- unit membership;
- request payload identity;
- duplicated reservations;
- unexpected physical attempts;
- replay/reexecution;
- terminal state preservation.

Nao declare exactly-once apenas pela ausencia de erro.

## Hash policy

- SHA256 quando apropriado;
- registrar caminho;
- registrar hash completo no relatorio tecnico final quando necessario;
- nao usar prefixo curto como unica prova de identidade;
- distinguir hash esperado de hash observado;
- nao recalcular/modificar artefatos para faze-los convergir.

## PASS / FAIL / BLOCK

- PASS = todas as afirmacoes materiais do escopo foram comprovadas.
- FAIL = uma afirmacao testada foi comprovadamente falsa, sem
  necessariamente existir risco de continuar a auditoria.
- BLOCK = nao e seguro avancar operacionalmente ou nao existe evidencia
  suficiente/consistente para autorizar a proxima acao.
- Ausencia de erro NAO equivale a PASS.
- UNKNOWN relevante deve impedir PASS.

## Output contract

O relatorio deve terminar, quando aplicavel, com estrutura parecida:

```
SUBTRANSLATE_EVIDENCE_AUDIT = PASS/FAIL/BLOCK
SCOPE = ...
CANONICAL_STATE = ...
CANONICAL_STATUS = ...
NEXT_ACTION = ...
CANDIDATE_COMMIT = ...
CANDIDATE_TREE = ...
RUNTIME_OPERATION = ...
RUNTIME_FAMILY = ...
ACCOUNTING_CANONICAL = ...
ACCOUNTING_RUNTIME = ...
ACCOUNTING_MATCH = YES/NO/NOT_APPLICABLE
AUTHORIZATION_SCOPE = ...
OBSERVED_SIDE_EFFECTS = ...
UNEXPECTED_EVIDENCE = [...]
STALE_CANONICAL_STATE = YES/NO
CANONICAL_STATE_RECONCILIATION_REQUIRED = YES/NO
PROTECTED_SURFACES_CHANGED = YES/NO/NOT_CHECKED
TESTED = [...]
NOT_TESTED = [...]
SIDE_EFFECTS_EXECUTED = NO
FILES_WRITTEN = []
MODEL_CALLS_EXECUTED = 0
TRANSPORTS_EXECUTED = 0
RETRIES_EXECUTED = 0
BLOCKERS = [...]
```

Nao force campos irrelevantes a uma auditoria especifica; adapte o relatorio
mantendo os principios.

## Relacao com commands/agentes

- agent = quem executa;
- command = entry point/convenience workflow;
- skill = procedimento reutilizavel especializado.

Esta skill nao substitui `preflight.md`, `substatus.md` nem os agentes
`subtranslate-audit`/`subtranslate-review`.
