# Subtranslate — Constituição Operacional dos Agentes

## Workspace autorizado

O workspace gravável desta linha de desenvolvimento é:

`/home/palhacinho/codex-projects/subtranslate-v238-candidate`

Branch esperada:

`candidate/v2.3.8`

Não tratar `main`, snapshots, diretórios de review, produção Docker, Library ou state real como áreas de desenvolvimento.

## Fonte de verdade

No início de toda tarefa relevante, reconcilie:

1. `@authority/PROJECT_STATE.json` — estado factual/canônico corrente.
2. `@authority/HANDOFF_CHATGPT.md` — cronologia e decisões anteriores.
3. Git da candidata — branch, HEAD, status e diff.
4. `@main` — baseline principal, somente quando comparação for necessária.
5. Testes e artefatos persistidos — evidência técnica.

Se essas fontes entrarem em conflito de forma material, pare em fail-closed e reporte a divergência. Não escolha silenciosamente uma interpretação.

O histórico pode conter estados com nomes como `CHATGPT_AUTHORIZATION_REQUIRED`.
Esses nomes históricos devem ser preservados como evidência, mas NÃO constituem autorização para o OpenCode.

Sob OpenCode, qualquer gate dessa classe deve ser interpretado operacionalmente como:

`USER_AUTHORIZATION_REQUIRED`

O agente não pode se autoautorizar.

## Operações que exigem autorização explícita

Não executar sem autorização explícita do usuário na sessão corrente:

- chamadas reais de geração/tradução em modelos;
- novas traduções ou retraduções;
- execução operacional de batches/gates ainda bloqueados;
- E07–E12 quando o estado canônico ainda exigir decisão;
- escrita na Library;
- escrita/migração do state real;
- deploy ou alteração de produção;
- restart/recreate de produção;
- promoção de candidata;
- alterações em `main`;
- remoções destrutivas;
- limpeza de evidências históricas;
- reescrita de histórico Git;
- push remoto.

Autorização para analisar ou editar código da candidata não implica autorização para qualquer operação acima.

## Trabalho permitido na candidata

Quando a tarefa solicitar implementação:

- trabalhar somente na candidata;
- fazer mudanças pequenas e rastreáveis;
- preservar comportamento fail-closed;
- preservar lineage, durabilidade e exactly-once onde aplicáveis;
- preservar evidência histórica;
- não transformar FAIL em PASS por relaxamento de validação;
- não fabricar resultados de testes, modelo ou runtime;
- preferir testes offline e determinísticos antes de qualquer prova operacional;
- interromper no primeiro blocker real quando o protocolo assim exigir.

## Git

Antes de editar:

- confirmar branch;
- confirmar `git status`;
- entender o diff existente.

Nunca executar automaticamente:

- `git reset`;
- `git clean`;
- `git push`;
- descarte de alterações;
- reescrita de histórico.

`git commit` só deve ocorrer quando a tarefa ou o usuário autorizar claramente o commit.

Não modificar a worktree `main` a partir desta candidata.

## Estado e documentação histórica

Não reescrever artefatos históricos apenas para fazê-los concordar com conclusões novas.

Quando uma evidência anterior estiver incorreta, preservar a evidência original e registrar a correção de forma aditiva conforme as convenções do projeto.

Não editar `PROJECT_STATE.json` ou `HANDOFF_CHATGPT.md` externos a partir deste workspace sem uma autorização específica de migração documental.

## Runtime evidence posterior ao estado canônico

Se runtime evidence posterior ao `PROJECT_STATE.json` provar calls, transports, mutations, blockers ou resultados materialmente ausentes do estado canônico:

- considerar o estado canônico stale para continuação operacional;
- preservar o estado anterior como histórico;
- marcar operacionalmente `CANONICAL_STATE_RECONCILIATION_REQUIRED`;
- bloquear novos side effects;
- bloquear novas model calls;
- bloquear batches;
- bloquear escrita em Library/state real/produção;
- reconciliar documentalmente as evidências antes de prosseguir;
- a existência da execução passada nunca constitui autorização retroativa.

## Critério para declarar PASS

Antes de declarar uma tarefa concluída:

1. revisar o diff;
2. executar os testes offline relevantes;
3. verificar que nenhum gate operacional proibido foi atravessado;
4. registrar claramente o que foi testado e o que não foi testado;
5. informar blockers ou riscos restantes;
6. não usar ausência de erro como prova de algo que não foi efetivamente testado.

## Agentes auxiliares

Use agentes de auditoria/review quando uma mudança envolver contratos canônicos, durabilidade, recovery, parsing, normalização, lineage, concorrência, transportes ou promoção de estado.

Auditores não corrigem silenciosamente o código que estão auditando.

## Comunicação

Responder em português brasileiro.

Manter código, paths, identificadores, comandos, nomes de modelos e termos técnicos no idioma original quando apropriado.

Para o usuário, explicar o resultado operacional de forma direta, sem exigir conhecimento de programação.
