# Contribuindo com o Transass

Obrigado pelo interesse em contribuir! Este documento define o fluxo de
contribuição e os padrões do projeto.

## Código de conduta

Ao participar, você concorda com o [Código de Conduta](CODE_OF_CONDUCT.md).

## Como contribuir

1. **Abra uma issue** descrevendo o problema ou a melhoria antes de grandes
   mudanças.
2. **Faça um fork** e crie uma branch descritiva (`fix/...`, `feat/...`).
3. **Mantenha mudanças pequenas e rastreáveis** — o projeto valoriza diffs
   revisáveis.
4. **Adicione testes offline** para qualquer mudança de comportamento.
5. **Rode a suíte offline** antes de abrir o PR:

```sh
PYTHONPATH=src/subtranslate python3 -m pytest tests/offline
```

6. Abra um **Pull Request** usando o template.

## Padrões do projeto

- **Imports planos**: os módulos de `src/subtranslate/` usam imports flat
  (`from pipeline_registry import ...`); rode com `PYTHONPATH=src/subtranslate`.
- **Fail-closed**: em caso de dúvida sobre segurança, durabilidade ou estado,
  prefira bloquear a operação a relaxar validação.
- **Evidência > ausência de erro**: não declare PASS sem provar o que foi
  testado.
- **Sem segredos**: API keys e configurações locais nunca entram no repositório
  (`.gitignore` cobre `.env`, `*api_key*`, `secrets/`).
- **Durabilidade**: mudanças no pipeline devem preservar exactly-once e a
  evidência forense por chamada.

## Testes

- `tests/offline/` — suítes determinísticas, sem rede, sem modelo (obrigatórias
  no CI).
- `tests/model/` — testes que exigem modelo/GPU; nunca parte do CI padrão.

## Reportando bugs

Use o template de [issue de bug](.github/ISSUE_TEMPLATE/bug_report.md) com
passos de reprodução, comportamento esperado vs observado e logs relevantes.

## Dúvidas

Abra uma issue com a tag `question` ou discuta em um PR existente.