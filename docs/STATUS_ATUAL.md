# Transass — baseline verificável

Este arquivo descreve o que o repositório pode provar. Saúde do Docker,
traduções em andamento e contagens de testes pertencem a relatórios datados de
diagnóstico — chamar um retrato antigo de “estado atual” é pedir para o gremlin
da documentação trabalhar no turno da noite.

## Fonte canônica

- Repositório: <https://github.com/Eltonfk/TRANSASS>
- Branch de desenvolvimento: `main`
- Versão declarada: consulte `src/subtranslate/_version.py`.
- Release publicada: consulte a página **Releases** do GitHub.
- Pipeline padrão para novos trabalhos: `v3` (`v3_0_0` nos artefatos).
- `legacy`, `v2_3_0` e `v2_3_8` permanecem apenas para replay/compatibilidade;
  configs existentes migram para V3 em memória sem reescrever evidência histórica.
- Artefatos registram `v3_0_0`; seletor e preflight normalizam essa versão para
  o plano `v3`, evitando retradução desnecessária.

## Como verificar o runtime

```sh
docker compose --env-file .env -f deploy/compose.yaml ps
curl --fail http://127.0.0.1:5050/health
curl --fail http://127.0.0.1:5050/version
```

Esses comandos observam o runtime presente; o Git, sozinho, não pode prometer
que um contêiner externo continua saudável ou que não há episódio em execução.
Uma suíte offline comprova contratos determinísticos, não substitui smoke test
autorizado no Docker/Desktop nem uma tradução real autorizada.

## Como verificar a qualidade

```sh
pytest tests/offline -q
PYTHONPATH=desktop/src pytest desktop/tests -q
```

A suíte offline não chama modelos nem serviços externos. Registre data, commit e
resultado quando uma execução precisar servir como evidência.

Status comprovado em 2026-09-29:
- `tests/offline`: 1.168 testes aprovados, 0 falhas, 38 deselecionados, 70 subtestes.
- `desktop/tests`: 31 testes aprovados (100%).
- Loteamento Cognitivo Adaptativo: implementado no V3 com orçamentos dinâmicos de densidade (palavras e caracteres) e decomposição recursiva (half-split) em caso de falha de formatação do modelo local.
- Falha histórica de fallback visual V2.3.8: saneada com cobertura determinística
  tanto para a reconstrução válida quanto para o fallback em falha forçada.
- Proteção de publicação de sidecar: validada e coberta contra sobrescrita indevida.

## Limites operacionais

- Escritas na Library, traduções reais, deploy e manutenção destrutiva exigem
  autorização consciente do operador.
- O modo `candidate_only=true` opera em staging isolado sob
  `STATE_DIR/candidate-source-staging/<job-id>/`, nunca grava registros no banco da
  Library e nunca substitui arquivos sidecar existentes (`allow_replace` estrito).
- Ledgers forenses, capturas duráveis e registros históricos são preservados; não
  são removidos automaticamente por limpezas superficiais.

## Relatórios datados de diagnóstico e incidentes operacionais

- [Auditoria e implementação 3.0.1 — segurança, validação e distribuição](reports/2026-09-30-audit-implementation-v3.0.1.md)

Os registros históricos detalhados de execuções de episódios, incidentes com modelos,
telemetria térmica e reconciliações de contêineres estão preservados em `docs/reports/`:

- [Shiki S01E08 — V3 Canary](reports/2026-09-27-shiki-e08-v3-canary.md)
- [Shiki S01E09 — Telemetria Térmica NVIDIA](reports/2026-09-27-nvidia-thermal-shiki-e09.md)
- [Shiki S01E10 — Incidente Candidate-Only e Isolamento de Staging](reports/2026-09-27-shiki-e10-candidate-only-incident.md)
- [Shiki S01E13 — Resposta Incompleta Ollama e JSON estrito](reports/2026-09-28-shiki-e13-ollama-incomplete-response.md)
- [Shiki S01E14 — Histórico de Tentativas e Reconciliações (r28 a r48)](reports/2026-09-28-shiki-e14-reconciliations-r28-to-r48.md)
- [Full-Time Magister S02E02 — falso positivo de quebra de palavra e atualização após a fila](reports/2026-09-30-full-time-magister-s02e02-line-break-validation.md)
