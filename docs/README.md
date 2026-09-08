# Documentação do Transass

Esta é a porta de entrada da documentação ativa. Arquivos em `docs/archive/`
são registros históricos: úteis para arqueologia, inadequados para instalar o
programa numa terça-feira à noite.

## Para usar

- [Instalação](INSTALLATION.md): Desktop, Docker e desenvolvimento.
- [Configuração](CONFIGURATION.md): providers, caminhos, idioma e Thermal Guard.
- [Operações](OPERATIONS.md): iniciar, atualizar, diagnosticar e manter.
- [Recovery e rollback](RECOVERY_AND_ROLLBACK.md): recuperação sem sacrificar a
  biblioteca ao deus do `rm -rf`.

## Para entender

- [Arquitetura](ARCHITECTURE.md)
- [Pipelines](PIPELINES.md)
- [Biblioteca e lineage](LIBRARY_AND_LINEAGE.md)
- [Contratos canônicos V2.3.8](canonical-contracts-v2_3_8.md)
- [Decisões arquiteturais](adr/)

## Para desenvolver e distribuir

- [Desktop](DESKTOP.md)
- [Testes](TESTING.md)
- [Segurança](SECURITY.md)
- [Contribuição](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)

O histórico de versões anteriores está em `releases/`; planos substituídos e
análises antigas permanecem isolados em `docs/archive/` e não representam o
comportamento atual.
