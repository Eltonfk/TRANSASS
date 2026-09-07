# Security boundaries

- Secrets and real credentials stay outside Git and reports.
- Real DBs, media, state, sidecars and production jobs stay outside Git.
- Path traversal and cross-episode lineage fail closed.
- Unknown pipeline IDs fail closed.
- Model-required tests are explicit and isolated.
- `.gitignore` excludes state, databases, credentials, caches and local output

Na linha Desktop, API keys são encaminhadas ao cofre do sistema quando o
backend `keyring` está disponível. Em ambientes headless/Docker, permanece o
arquivo compatível com modo `600`; a API expõe apenas indicadores booleanos de
credencial configurada. O endpoint de diagnóstico remove keys e caminhos
absolutos antes do download.
  without globally ignoring legitimate fixture subtitle files.
