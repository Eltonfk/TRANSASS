---
description: Verifica se uma tarefa pode ser iniciada com seguranca antes de qualquer edicao.
agent: plan
---

Faca o preflight da tarefa descrita pelo usuario.

Antes de propor ou executar qualquer alteracao:

- leia o AGENTS.md;
- leia o estado corrente em `@authority/PROJECT_STATE.json`;
- confira branch, HEAD e git status;
- identifique o gate corrente;
- determine se a tarefa e somente offline/codigo ou se atravessa um gate operacional;
- identifique testes offline relevantes;
- liste qualquer autorizacao explicita ainda necessaria.

Nao modifique arquivos.

Ao final responda somente com:
- READY, se a tarefa pode prosseguir dentro da autoridade atual;
- BLOCKED, se exige autorizacao adicional ou existe divergencia;

seguido da justificativa objetiva.
