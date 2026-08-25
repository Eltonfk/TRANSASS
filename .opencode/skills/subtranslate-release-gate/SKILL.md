---
name: subtranslate-release-gate
description: Auditoria read-only de readiness de release do Subtranslate - valida canonical state, runtime evidence, testes/gates, accounting, candidate/main/production e blockers antes de qualquer promocao. Nunca executa promocao.
---

# subtranslate-release-gate

## Principios

1. AGENTS.md ja foi carregado e permanece soberano. Esta skill nao o substitui.
2. Esta skill e um GATE READ-ONLY.
3. Carregar esta skill nunca autoriza side effects.
4. Trabalhe fail-closed: em caso de duvida material, BLOCK.
5. Readiness tecnico e autorizacao operacional sao dimensoes separadas.

SKILL_INSTRUCTION_IS_NOT_AUTHORIZATION = true

Carregar esta skill nunca autoriza:

- release;
- promotion;
- merge;
- deploy;
- restart;
- production write;
- Library write;
- main write;
- state write;
- PROJECT_STATE/HANDOFF write;
- model call;
- transport;
- retry;
- batch execution;
- code change;
- commit;
- push;
- destructive cleanup.

A skill apenas determina se uma promocao/release ESTA PRONTA para ser
considerada. A promocao concreta sempre exige workflow e autorizacao
separados.

## Papeis

- agent = quem executa;
- command = entry point / workflow conveniente;
- skill = procedimento reutilizavel de decisao de release;
- promotion/deploy executor = mecanismo separado que produz side effects
  somente apos autorizacao explicita.

Esta skill nao substitui AGENTS.md, `subtranslate-evidence-audit`,
`subtranslate-canary`, `subtranslate-canonical-reconciliation`,
`subtranslate-build` nem `subtranslate-review`.

## Proposito

Determinar, estritamente read-only, se uma candidata esta tecnicamente
pronta para ENTRAR EM UM GATE DE PROMOCAO.

Ela NAO promove.

Ela responde:

RELEASE_GATE = PASS/FAIL/BLOCK

com evidencias claras.

PASS significa: todos os criterios obrigatorios do escopo foram comprovados.
Nao significa: "deploy autorizado".

## Release readiness vs release authorization

Distinguir obrigatoriamente:

RELEASE_READINESS = estado tecnico comprovado
RELEASE_AUTHORIZATION = decisao operacional explicita separada

A skill so pode produzir RELEASE_READINESS.

Mesmo RELEASE_GATE = PASS nao significa autorizacao para:

merge, deploy, promotion, production write, Library write, restart, push.

## Modos

MODE = RELEASE_PREFLIGHT_READ_ONLY
MODE = RELEASE_GATE_AUDIT
MODE = PROMOTION_PLAN_READ_ONLY

Nao existe modo de promocao real nesta skill. Qualquer execucao concreta
deve ocorrer fora dela e somente apos autorizacao explicita.

## Canonical state first

Antes de considerar release, ler e validar quando aplicavel:

- PROJECT_STATE.json;
- HANDOFF_CHATGPT.md.

Determinar:

- current_operation;
- state;
- status;
- latest_decision;
- next_action;
- release/version candidate;
- accounting;
- pending gates;
- authorizations.

Se canonical state estiver stale:

RELEASE_GATE = BLOCK
CANONICAL_STATE_RECONCILIATION_REQUIRED = YES

Nenhuma promocao pode seguir.

## Runtime evidence

Quando relevante, provar:

- operation/family;
- ledger;
- batch/call states;
- terminal states;
- reservations;
- retries;
- unknown outcomes;
- transport failures;
- checkpoints;
- mission completion;
- runtime evidence chronology;
- exactly-once invariants.

Pode carregar `subtranslate-evidence-audit` para auditoria profunda.
Nao duplicar integralmente essa skill.

## Required work complete

Identificar o trabalho obrigatorio ainda pendente.

Quando aplicavel:

- episodes;
- batches;
- gates;
- human review;
- offline tests;
- runtime tests;
- canonical reconciliation;
- Library preparation;
- release documentation.

Se qualquer item obrigatorio estiver:

NOT_STARTED
STARTED / NOT COMPLETE
USER_AUTHORIZATION_REQUIRED
HUMAN_REVIEW_REQUIRED
BLOCKED
UNKNOWN

a release nao pode receber PASS, a menos que o projeto declare
explicitamente aquele item nao obrigatorio.

## Candidate identity

Auditar:

- branch;
- HEAD;
- tree;
- tracked diff;
- untracked relevantes.

Confirmar que a candidata avaliada e exatamente a candidata descrita pela
documentacao/evidence.

Divergencia: BLOCK. Nao alterar Git.

## Test gates

Identificar os testes obrigatorios reais da candidata.

Quando aplicavel:

- offline suites;
- targeted tests;
- regression tests;
- schema/contract tests;
- runtime validation;
- human playback/review;
- data integrity checks.

Nao exigir nomes hard-coded que nao existam no projeto atual.

Para cada gate:

NAME
REQUIRED = YES/NO
STATUS = PASS/FAIL/NOT_RUN/UNKNOWN
EVIDENCE = ...

Ausencia de erro != PASS.

## Accounting / budget

Auditar accounting relevante, quando aplicavel:

- global historical transports;
- mission model calls;
- operation transports;
- retries;
- initial consumed;
- retry consumed;
- reservations;
- unknown outcomes;
- failures/cancellations.

Confirmar que accounting canonico e runtime convergem.

ACCOUNTING_MATCH = NO -> BLOCK.

## Canonical reconciliation

Se runtime evidence estiver a frente da documentacao:

- usar/recomendar `subtranslate-canonical-reconciliation`;
- nao aplicar automaticamente.

Release gate deve retornar BLOCK ate reconciliacao concluida.

## Historical evidence

Nao permitir release se:

- evidence necessaria foi apagada;
- lineage nao fecha;
- physical attempts inesperados existem;
- replay/reexecution nao explicado existe;
- historico foi reescrito;
- terminal states sao inconsistentes.

Nao "limpar" evidencia para passar no gate.

## Main / baseline

Auditar quando relevante:

- current candidate;
- main;
- merge-base;
- commits ahead/behind;
- expected baseline.

Nao modificar main. Nao fazer merge.
Nao assumir que "ahead" significa pronto para promocao.

## Production

Release gate pode AUDITAR producao quando necessario.

Pode verificar read-only:

- container status;
- image identity;
- restart count;
- health;
- queue state;
- database integrity;
- production version.

Nao pode: restart, deploy, pull, stop, start, write, migrate.

Producao alterada inesperadamente: BLOCK.

## Library / state real

Se a promocao exigir Library/state real:

- verificar readiness e pre-condicoes.

Esta skill nao pode escrever nesses locais.

Se Library/state real ainda nao estiver autorizado/preparado:

- registrar gate pendente;
- nao fabricar PASS.

## Human review

Quando uma etapa exige confirmacao humana, a skill nao pode substituir o
humano.

Retornar:

HUMAN_REVIEW_REQUIRED

quando aplicavel.

Uma confirmacao historica deve estar registrada nas fontes de autoridade
para ser considerada.

## Promotion plan read-only

Se RELEASE_GATE = PASS, a skill pode produzir um plano read-only contendo:

- candidate identity;
- target baseline;
- required backup;
- exact promotion steps;
- post-promotion validation;
- rollback boundaries;
- documentation reconciliation;
- protected surfaces;
- authorization points.

Mas:

PROMOTION_EXECUTED = NO

## Release gate criteria

PASS somente se:

- canonical state atual e reconciliado;
- trabalho obrigatorio concluido;
- candidate identity comprovada;
- tests/gates obrigatorios PASS;
- runtime evidence consistente;
- accounting convergente;
- exactly-once/lineage aceitaveis;
- blockers = [];
- required human review concluida;
- protected surfaces sem alteracao inesperada;
- nenhuma pendencia obrigatoria.

FAIL quando uma verificacao concreta provar requisito nao atendido.

BLOCK quando:

- evidence insuficiente;
- canonical stale;
- estado material UNKNOWN;
- identidade diverge;
- autorizacao/gate humano pendente impede decisao;
- risco operacional impede considerar promocao.

## Current incomplete work

Reconhecer corretamente que uma candidata com trabalho ainda em progresso
NAO esta pronta para release.

Exemplo generico:

mission STARTED / NOT COMPLETE
pending batches
pending next_action
future side effects not authorized

deve resultar em:

RELEASE_GATE = BLOCK

ou FAIL conforme a semantica da evidencia.

Nao tentar "prever" que os passos restantes provavelmente passarao.

## Integracao com outras skills

Quando apropriado:

- `subtranslate-evidence-audit` -> prova evidence/runtime;
- `subtranslate-canary` -> procedimentos de batch canario;
- `subtranslate-canonical-reconciliation` -> resolve stale canonical
  documentation;
- `subtranslate-release-gate` -> decide readiness de release.

Nao misturar responsabilidades.

## Output contract

O relatorio deve terminar, quando aplicavel:

SUBTRANSLATE_RELEASE_GATE = PASS/FAIL/BLOCK

MODE = RELEASE_GATE_AUDIT

RELEASE_READINESS = READY/NOT_READY/UNKNOWN
RELEASE_AUTHORIZATION_GRANTED = NO

CANONICAL_STATE = ...
CANONICAL_STATUS = ...
NEXT_ACTION = ...

CANDIDATE_BRANCH = ...
CANDIDATE_COMMIT = ...
CANDIDATE_TREE = ...

MAIN_BASELINE = ...

CANONICAL_STATE_STALE = YES/NO
CANONICAL_STATE_RECONCILIATION_REQUIRED = YES/NO

REQUIRED_WORK = [...]
COMPLETED_WORK = [...]
PENDING_WORK = [...]

TEST_GATES = [...]

RUNTIME_EVIDENCE_STATUS = ...
ACCOUNTING_MATCH = YES/NO/NOT_APPLICABLE
LINEAGE_STATUS = ...

HUMAN_REVIEW_REQUIRED = YES/NO
HUMAN_REVIEW_STATUS = ...

PRODUCTION_STATUS = ...
LIBRARY_STATUS = ...

PROTECTED_SURFACES_CHANGED = YES/NO/NOT_FULLY_CHECKED

PROMOTION_PLAN_READY = YES/NO
PROMOTION_EXECUTED = NO

MODEL_CALLS_EXECUTED = 0
TRANSPORTS_EXECUTED = 0
RETRIES_EXECUTED = 0
FILES_WRITTEN = []

BLOCKERS = [...]

Nao force campos irrelevantes; adapte mantendo os principios.

## Relacao com commands/agentes

- agent = quem executa;
- command = entry point / convenience workflow;
- skill = procedimento reutilizavel de decisao de release;
- promotion/deploy executor = mecanismo separado.

Esta skill nao substitui `preflight.md`, `substatus.md`, `subtranslate-evidence-audit`,
`subtranslate-canary`, `subtranslate-canonical-reconciliation`,
`subtranslate-build` nem `subtranslate-review`.
