# Limites de segurança

- segredos e credenciais reais ficam fora do Git;
- mídia, bancos, sidecars e estado operacional ficam fora da imagem;
- caminhos são confinados às raízes autorizadas;
- lineage não cruza episódios;
- IDs de pipeline desconhecidos falham fechados;
- testes com modelos são opt-in;
- diagnósticos removem chaves e caminhos absolutos.

No Desktop, API keys usam o keyring do sistema quando disponível. Em ambiente
headless/Docker, o arquivo compatível recebe permissão restrita; a API devolve
somente indicadores de configuração.

O serviço web não possui autenticação para exposição pública. Restrinja-o à
máquina/LAN ou coloque autenticação e TLS num reverse proxy. A internet é um
lugar maravilhoso, mas não precisa conhecer sua biblioteca de anime.

Para divulgação de vulnerabilidades, siga a política em `../SECURITY.md`.
