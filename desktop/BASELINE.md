# Transass Desktop — baseline

> Documento histórico do ponto de partida. Não descreve a versão corrente.

Esta pasta inicia a linha Desktop a partir do snapshot web `v2.5.0`.

| Campo | Valor |
|---|---|
| Versão congelada | `v2.5.0` |
| Commit | `b5166b937d284f38edf1f43a9ec3c9c9261bd2ba` |
| Branch de origem | `candidate/v2.3.8` |
| Tag de origem | `v2.5.0` |
| Versão Desktop inicial | `2.5.0` |

O congelamento é lógico e documental nesta etapa. Nenhuma tag, branch,
artefato de release ou estado de produção foi alterado.

## Política de versão

`pyproject.toml`, `src/subtranslate/_version.py` e o shell Desktop usam
`2.5.0` como versão pública do produto. O identificador `v2_3_8` permanece
somente para o pipeline canônico e para a branch de origem; a tag histórica
`v2.5.0` não é reescrita.

## Regras

- O motor existente permanece intacto até haver testes de regressão equivalentes.
- Nenhuma chamada real de modelo faz parte do build ou dos testes de CI.
- O estado do usuário e as legendas de mídia nunca pertencem ao diretório de
  instalação.
