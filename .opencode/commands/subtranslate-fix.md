---
description: Diagnostica e corrige de forma gated a causa que bloqueou o Subtranslate.
agent: subtranslate-build
---

Ative `ERROR_DIAGNOSIS_AND_CORRECTION_PROFILE` em modo correcao controlada.

Antes de qualquer diagnostico, execute exatamente uma vez o helper read-only
allowlisted:
`python3 /home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/tools/subtranslate_context_inspect.py --summary`

Use o resumo compacto como fonte para estado canônico, runtime e higiene.
Nunca rode `python3 -c`, `python -c`, `cat`, `sed`, `jq`, `head`, `tail` ou shell
arbitrário para abrir, reformatar, extrair ou hashear PROJECT_STATE.json,
HANDOFF_CHATGPT.md ou runtime evidence. Se o resumo não for suficiente, pare em
`DIAGNOSTICO_INSUFICIENTE` e peça tooling read-only específico; não improvise.

Se esta for a primeira invocacao para o blocker corrente, faca somente
diagnostico/preflight, produza escopo exato, backup, testes, rollback e renderize
o HUMAN_GATE. Nao escreva ainda.

Se houver HUMAN_GATE corrente e a mensagem humana posterior for exatamente
`AUTORIZAR`, revalide o prestate e aplique somente a correcao declarada. Depois
teste, revise, informe todos os arquivos alterados e retorne o usuario a
`/subtranslate-next`. Qualquer divergencia gera FAIL_STOP explicado pelo bloco
obrigatorio de linguagem simples.

Nunca autorize ou execute automaticamente model call, transporte, retry,
resend, producao, Library, B5, B6 ou B7.

Quando `context_hygiene.status=REVIEW`, isto não é erro operacional e não
autoriza limpeza. Explique o motivo concreto e indique
`AUTO-03D-OPENCODE-ACTIVE-CONTEXT-HYGIENE-PREFLIGHT-R1` apenas quando houver
motivos ativos. O preflight é read-only; transferência para history,
compactação ou escrita documental exige gate próprio.

Contexto adicional do usuario:
$ARGUMENTS
