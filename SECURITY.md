# Política de Segurança do Transass

## Versões suportadas

| Versão | Suporte |
|---|---|
| 2.4.x | ✅ Ativa |
| < 2.4 | ❌ Sem suporte |

## Reportando vulnerabilidades

**Não abra issues públicas para vulnerabilidades de segurança.**

Envie um e-mail para o mantenedor (veja o perfil do autor no GitHub) com:

- Descrição da vulnerabilidade e impacto potencial;
- Passos de reprodução (sem expor dados sensíveis);
- Versão afetada;
- Sugestão de correção, se houver.

Você receberá uma resposta em até **7 dias úteis**. Após a correção, a
vulnerabilidade será divulgada publicamente com crédito ao reportante, se
desejado.

## Modelo de segurança

- **API keys**: armazenadas em arquivo host-local (`transport_config.json`,
  permissão `600`), **nunca** expostas pela API (apenas `keys_configured`).
- **Rede**: o app web é projetado para uso em **rede local** (bind LAN). Não
  exponha à internet sem autenticação/reverse proxy.
- **Path traversal**: endpoints de arquivo validam caminhos contra raízes
  autorizadas (`_authorized_path`, `_safe_relative`).
- **Segredos no repositório**: `.gitignore` bloqueia `.env`, `*api_key*`,
  `secrets/`, `credentials/`, `*.key`, `*.pem`.
- **Durabilidade**: o pipeline é fail-closed — falhas de transporte/validação
  nunca são silenciadas; a evidência por chamada é preservada.

## Práticas recomendadas para deploy

1. Use o Dockerfile com `--pull=false` e base pinada por digest.
2. Mantenha o `.env` fora do repositório (host-local).
3. Se expor além da LAN, adicione autenticação (ex.: reverse proxy com
   basic auth ou OIDC).
4. Atualize a imagem regularmente para receber correções.