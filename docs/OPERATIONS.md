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
docker build --pull=false -f deploy/Dockerfile -t transass:v2.5.2 .
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

Depois de conferir os candidatos e fazer um backup do estado, a remoção pode
ser solicitada explicitamente com `--apply`. A ferramenta recusa a operação se
houver tradução ativa, protege referências duráveis e bloqueia caminhos fora do
state. O Compose permite ajustar `TRANSASS_RETENTION_RUN_DAYS`,
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
