---
description: Executa o gate documental separado de reconciliacao canonica.
agent: subtranslate-doc-sync
---

Este e um gate DOCUMENTAL/CANONICO separado do `/subtranslate-next`.

Modo obrigatorio: `DOCUMENTAL_APPLY` somente nos dois arquivos:

- `@authority/PROJECT_STATE.json`
- `@authority/HANDOFF_CHATGPT.md`

Antes de qualquer escrita:

1. Exija a autorizacao literal `AUTORIZAR` nesta invocacao corrente. Nao aceite
   `AUTO-03C-...` como token de state machine e nao aceite autorizacao historica.
2. Confirme que o usuario forneceu o escopo documental corrente e o preflight
   read-only correspondente. Se faltar snapshot/preflight, termine em
   `DOCUMENTATION_WRITE_BLOCKED` sem escrever.
3. Preserve integralmente E1, historico e hashes; registre a proveniencia como
   `UNDOCUMENTED`; mantenha `blocker_declared_resolved=false`,
   `investigation_required=true` e
   `NEXT_ACTION=EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND`.
4. Nao altere runtime evidence, episode-budget.json, releases, current,
   manifest, Library, producao, main ou Git.

Construa os candidatos integralmente em memoria, produza dry-run e backup dos
dois arquivos. So depois aplique atomicamente, valide JSON, append-only do
HANDOFF, objeto aditivo, campos KEEP e ausencia de outros arquivos alterados.

BACKUP_ATTEMPT_BUDGET = 1 por arquivo. Crie primeiro o backup do HANDOFF e
depois o do PROJECT_STATE, sem paralelismo. Qualquer erro de serializacao,
truncamento, permissao ou validacao encerra imediatamente em
`DOCUMENTATION_WRITE_BLOCKED` com `BACKUP_FAILURE`; nao repita a chamada, nao
reenvie o conteudo e nao tente outro diretorio.

Para evitar transportar o conteudo dos arquivos pelo chat, use exclusivamente
o helper allowlisted:
`python3 /home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/tools/subtranslate_canonical_backup.py --run`
O helper retorna JSON com hashes e estado dos dois backups. Se ele retornar
falha ou exit diferente de zero, termine em `BACKUP_FAILURE` sem retry.

Se Write/Edit nao estiver disponivel para os dois caminhos exatos, termine em
`DOCUMENTATION_WRITE_BLOCKED`; nao use outro Bash, sudo, fallback ou workaround.

Retorne um relatorio `AUTO-03C-CANONICAL-RECONCILIATION-DOCUMENTARY-WRITE-R1`
com hashes pre/post, arquivos escritos, backup, escopo, verificacoes e
`NEXT_ACTION`. Nao avance para nenhum gate operacional.

$ARGUMENTS
