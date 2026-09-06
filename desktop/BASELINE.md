# Transass Desktop — baseline

Esta pasta inicia a linha Desktop a partir do snapshot web `v2.5.0`.

| Campo | Valor |
|---|---|
| Versão congelada | `v2.5.0` |
| Commit | `b5166b937d284f38edf1f43a9ec3c9c9261bd2ba` |
| Branch de origem | `candidate/v2.3.8` |
| Tag de origem | `v2.5.0` |
| Versão Desktop inicial | `3.0.0-alpha.1` |

O congelamento é lógico e documental nesta etapa. Nenhuma tag, branch,
artefato de release ou estado de produção foi alterado.

## Divergência conhecida

O arquivo raiz `pyproject.toml` ainda informa `2.4.9`, embora o módulo
`src/subtranslate/_version.py` informe `2.5.0`. Essa correção pertence à linha
Desktop e não deve modificar o snapshot histórico.

## Regras

- O motor existente permanece intacto até haver testes de regressão equivalentes.
- Nenhuma chamada real de modelo faz parte do build ou dos testes de CI.
- O estado do usuário e as legendas de mídia nunca pertencem ao diretório de
  instalação.
