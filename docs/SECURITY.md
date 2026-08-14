# Security boundaries

- Secrets and real credentials stay outside Git and reports.
- Real DBs, media, state, sidecars and production jobs stay outside Git.
- Path traversal and cross-episode lineage fail closed.
- Unknown pipeline IDs fail closed.
- Model-required tests are explicit and isolated.
- `.gitignore` excludes state, databases, credentials, caches and local output
  without globally ignoring legitimate fixture subtitle files.
