# Operações

## Verificação rápida

```sh
docker compose --env-file .env -f deploy/compose.yaml ps
curl -fsS http://127.0.0.1:5050/health
docker logs --tail 200 transass
```

Uma atualização profissional registra commit, versão, checksum do artefato,
digest da imagem e resultado dos testes. “Era o arquivo novo, eu acho” não
entra no relatório.

## Atualizar Docker

```sh
docker build --pull=false -f deploy/Dockerfile -t transass:v3.0.1 .
export TRANSASS_IMAGE=transass:v3.0.1
docker compose --env-file .env -f deploy/compose.yaml up -d --force-recreate
```

Depois confirme `/health`, acesso à mídia e persistência do acervo.

## Higienização do estado

A retenção trata somente artefatos gerados pelo runtime. Ela não remove mídia,
legendas publicadas, acervo, glossário, SQLite ou a configuração do motor.
Primeiro gere um relatório; a operação é dry-run por padrão:

```sh
python3 scripts/transass_maintenance.py --state-dir /caminho/para/STATE_DIR --json
```

Em um contêiner em execução:

```sh
docker exec --user 1000 transass \
  python3 scripts/transass_maintenance.py --state-dir /app/state --json
```

Depois de conferir os candidatos e fazer um backup, pare o serviço antes de
solicitar `--apply`, com autorização explícita do operador. Não use `--apply`
via `docker exec` no serviço em execução. A leitura acima continua permitida.

A limpeza usa um lock exclusivo entre processos, relê `jobs.json`, verifica
identidade dos alvos e recusa links, categorias incompatíveis e referências
duráveis. O inventário inclui `v3-runs` e `v238-runs`; evidência referenciada por
jobs históricos não vira lixo por ter envelhecido. No Windows, a aplicação de
remoções permanece bloqueada quando as APIs de exclusão ancorada não estão
disponíveis; o relatório funciona normalmente. Nenhuma limpeza é automática.

O Compose permite ajustar `TRANSASS_RETENTION_RUN_DAYS`,
`TRANSASS_RETENTION_STAGING_DAYS`, `TRANSASS_RETENTION_TRANSIENT_HOURS`,
`TRANSASS_RETENTION_FAILURE_LEDGER_JOBS` e `TRANSASS_RETENTION_CONFIG_BACKUPS`.

## Diagnóstico

A aba Diagnóstico mostra falhas relevantes e permite exportar um pacote
sanitizado. Telemetria térmica contínua fica no log; a interface só destaca o
guardião quando há alerta, backoff ou trip.

Ao reportar problema, envie:

- versão e SHA-256 do AppImage/instalador;
- provider e modelo, sem chave;
- trecho do log desde “Fila criada” até a falha;
- legenda fonte quando o problema for de classificação.

## Backup

Pare o serviço antes de copiar `STATE_DIR`. Mídia e sidecars estão em
`MEDIA_ROOT`; trate-os separadamente. API keys não devem entrar em anexos,
issues, screenshots ou naquela mensagem de madrugada que parecia uma boa ideia.

Inclua `STATE_DIR/audits/` no backup. Os arquivos são evidência detalhada; o
resumo em `jobs.json` não pretende substituí-los. Um job com estágio
`RECONCILIATION_REQUIRED` não foi publicado com sucesso e deve ser investigado
antes de uma nova tentativa sobre o mesmo destino.
