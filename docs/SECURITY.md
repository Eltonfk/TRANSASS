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

Manutenção destrutiva requer serviço parado e lock de estado exclusivo. Um
relatório antigo não autoriza apagar um alvo que mudou, ganhou referência ou
passou a ser usado. Links e junctions não são atalhos para atravessar esse gate.

Fallback exige evidência de zero chamadas físicas e nenhuma captura/saída do
primário. Timeout depois do envio pode exigir reconciliação, não uma segunda
tentativa silenciosa. Cancelamento interrompe efeitos futuros; uma operação
que já passou pelo gate de gravação não pode ser desfeita pelo botão “Parar”.
