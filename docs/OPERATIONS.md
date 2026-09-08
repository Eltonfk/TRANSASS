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
docker build --pull=false -f deploy/Dockerfile -t subtranslate:v2.5.1 .
docker compose --env-file .env -f deploy/compose.yaml up -d --force-recreate
```

Depois confirme `/health`, acesso à mídia e persistência do acervo.

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
