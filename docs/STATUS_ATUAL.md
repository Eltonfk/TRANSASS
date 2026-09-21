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
- Pipeline padrão para novos trabalhos: `v2_3_8`.

## Como verificar o runtime

```sh
docker compose --env-file .env -f deploy/compose.yaml ps
curl --fail http://127.0.0.1:5050/health
curl --fail http://127.0.0.1:5050/version
```

Esses comandos observam o runtime presente; o Git, sozinho, não pode prometer
que um contêiner externo continua saudável ou que não há episódio em execução.

## Como verificar a qualidade

```sh
PYTHONPATH=.:src/subtranslate python3 -m pytest tests/offline -q
PYTHONPATH=.:src/subtranslate:desktop/src python3 -m pytest desktop/tests -q
```

A suíte offline não chama modelos nem serviços externos. Registre data, commit e
resultado quando uma execução precisar servir como evidência.

## Limites operacionais

- Escritas na Library, traduções reais, deploy e manutenção destrutiva exigem
  autorização consciente do operador.
- O Compose escuta somente em `127.0.0.1` por padrão. Acesso pela LAN deve passar
  por firewall e proxy autenticado; a interface web ainda é administrativa.
- Ledgers forenses não devem ser apagados só porque ficaram com cara de sótão.
  Use a manutenção em modo `dry-run` e revise o relatório antes de `--apply`.
