# Auditoria e implementação — Transass 3.0.1

Data: 30/09/2026. Base: `main`, `e4ad5efb77c729844c7086f72f22166608c53e55`.
Escopo autorizado: corrigir achados da auditoria anterior, consolidar Git,
push/tag e atualizar instaladores Windows/Linux. Sem novas traduções,
publicação no acervo, limpeza histórica ou atualização da produção.

## Resultado para quem usa

Foram corrigidas decisões contraditórias, não criada outra arquitetura. Uma
legenda que ainda perdeu aspas, tags ou tradução não é declarada válida só
porque passou por um reparo. Os dados do usuário saem do diretório padrão de
instalação Windows. A limpeza não pode usar um relatório antigo para apagar
uma pasta que mudou ou passou a ser usada.

O V3 continua reutilizando componentes históricos quando necessário. Apagá-los
agora ou remover testes não reduziria o custo do modelo de forma comprovada.

## Rastreamento dos achados necessários

| Achado | Correção implementada | Prova determinística |
| --- | --- | --- |
| A — desinstalador podia apagar dados | `Programs/Transass`, sem exclusão ampla, recusa de logs/diretórios legados | Layout/manifesto e sentinelas; testes nativos Windows preparados |
| B — limpeza seguia links/relatório antigo | Lock de processo, jobs atuais, categorias, identidade e exclusão ancorada | Troca de alvo, link de ancestral, job novo, estado inválido, serviço ocioso vivo |
| C — aspas/parênteses inválidos aprovados | Mesma regra completa após reparo e antes/depois de serializar; posse por evento | Delimitador invertido, perdido, deslocado e citação entre eventos |
| D — diálogo title-case copiado | Identidade só com evidência de nome/título protegido | `Stop Laughing!`, `Kill Him!` e nomes legítimos |
| E — placas diferentes agrupadas | Chave inclui pontuação/controles e valida membros antes de aplicar | Pergunta versus exclamação; grupos forjados; placas equivalentes |
| F — tags presentes com alcance errado | Ordem e ancoragem verificadas; gradiente conserva tags não relacionadas à cor | Itálico invertido/deslocado; escopo colapsado; corpus estilizado positivo |
| G — parada ainda arquivava/publicava | Gate coordenado em cada efeito; operação já iniciada declarada como tal | Corridas na auditoria, ingest, lineage e publicação |
| H — timeout podia repetir o episódio | Regra compartilhada exige zero chamadas físicas e nenhuma captura/saída | Timeout após cliente físico, captura persistida e resultado sem arquivo |
| I — manifests discordavam | 3.0.1 alinhada e `check_release.py` local/CI/tag | Deriva de imagem e tag bloqueadas |
| J — runner omitia pytest | Atalho local e shards usam pytest e mesma política | Função livre que falha; coleta vazia com exit code 5 |
| K — páginas/seleção/selos incoerentes | Atualiza páginas carregadas, compartilha carga em voo, interrompe erros | JavaScript real com API falsa; seleção 60/80 mantida; concorrência e fonte plana |
| L — retenção não inventariava V3 | `v3-runs` incluído, protegendo hashes de runs e referências históricas | Run órfão versus referenciado; ledger de job antigo preservado |
| M — migração bloqueada pela inicialização | Migra antes de criar Library/ledger e coordena destino; lock não é dado | Destino vazio/ocupado, backup, frozen e exclusão entre processos |
| N — wheel incompleto | Dependências explícitas e assets completos | Wheel instalado fora do checkout, imports e assets servidos |

Também foi preservada a correção lexical do Full-Time Magister S02E02:
`irá`/`ira` podem ser palavras completas antes de uma quebra, sem liberar cortes
de palavras conhecidas. O relato anterior e sua candidata não foram reescritos
nem publicados no acervo.

Uma tentativa de replay estrito das solicitações antigas parou no primeiro
reparo novo: o modelo havia copiado o título da série e a nova regra exige
evidência para autorizar identidade. Isso **não foi uma tradução nova**, nem
se reutilizou uma resposta para uma solicitação diferente como se fosse igual.

A interface V3 passou a receber o título confirmado pelo acervo. Evidência
conservadora de nomes ingleses repetidos no meio de cláusulas permite identidade,
sem obrigar a conservar em inglês títulos genéricos como “Music School”.
Com idioma real inglês e título confirmado, a prova de corpus armazenado
validou os **219 eventos** do E02 no novo núcleo, com hashes das **28 capturas**
conferidos, validação de memória/serialização e comparação estrutural independente.
Todas as palavras das traduções capturadas foram preservadas. O arquivo e
relatório ficaram somente em `/tmp/transass-e02-20260930-SxPzAJ/`, sem Library.
Essa prova é de corpus/validação, **não replay de solicitações idênticas nem
nova chamada ao Qwen**. SHA-256 da saída de corpus:
`9d75742300dedcfa79b0b3dccce5c1ffa6a37993545810c8406d548dcbe49b7f`.

## Eficiência e espaço

- A assinatura de ancoragem e montagem de gradientes usam passada linear,
  evitando reprocessar prefixos crescentes em eventos densos. A regressão
  comprova a quantidade de texto percorrido; não promete percentual de aceleração.
- Placas continuam compartilhando traduções realmente equivalentes.
- Chamada ambígua não dispara fallback repetindo uma execução de custo incerto.
- Paginação falha uma vez e informa o erro, em vez de repetir indefinidamente.
- Artefatos do CI ficam restritos à release, sem bundle duplicado. O prazo de
  14 dias afeta artefatos temporários; assets publicados têm ciclo separado.
- A manutenção permanece manual/dry-run por padrão. Não houve limpeza real.

## Verificação e limites

Suítes locais e novas regressões são executadas antes do commit final, com
logs em `/tmp/transass-release-v3.0.1-*.log`. O gate remoto é composto por quatro
shards offline, build Docker e runners nativos Linux/Windows. Os instaladores
só devem ser publicados depois do sucesso dos builds e smokes respectivos.

Resultados locais finais: **1.358 testes offline aprovados**, 38 deselecionados
pela política histórica/estresse, 70 subtestes; **51 testes Desktop aprovados**
e dois testes Inno nativos pendentes de Windows. O teste local de socket usou
permissão para localhost após a restrição inicial do sandbox. Beta smoke
aprovou health, caminho com acentos/espaços, onboarding, diagnóstico e falha
esperada do provider offline. Compilação, sintaxe JavaScript/shell, identidade
3.0.1 e `git diff --check` passaram. O gate offline final levou 61,61 segundos;
não é medição de desempenho de tradução.

Os revisores auxiliares foram interrompidos pelo limite da ferramenta antes
de entregar parecer final. O agente principal revisou as alterações e as
regressões; não se apresenta isso como parecer independente concluído.

Não foram medidos novos tempos de tradução, temperaturas reais ou precisão
linguística de modelos. O wheel usa dependências disponíveis no ambiente de
testes: seu teste isolado prova layout/imports/recursos, não download de
dependências em rede. Testes Windows nativos precisam executar no runner;
não são declarados aprovados só por existirem no repositório.

Uma operação final já iniciada pode concluir após a parada; a API/interface
informam esse limite e bloqueiam as próximas. Cancelamento não desfaz arquivos
já publicados nem registros já gravados. Estado parcial é preservado para
diagnóstico, não convertido silenciosamente em conclusão completa.

No Windows, o relatório de manutenção funciona, mas `--apply` permanece
indisponível sem exclusão ancorada segura. No Linux, exige serviço parado,
backup e autorização. Uma instância de serviço mantém lease mesmo sem fila.

## Próximo passo operacional

Usar os novos instaladores e revisar uma candidata em operação real somente
quando houver autorização específica para modelo, episódio e orçamento. Não
há recomendação de reescrita, eliminação do legado ou limpeza automática.

## Complemento — primeiro gate remoto

O commit `e0996fc` passou nos quatro shards offline e no build Docker da
[CI 36761906002](https://github.com/Eltonfk/TRANSASS/actions/runs/36761906002).
No [build Desktop 36761906537](https://github.com/Eltonfk/TRANSASS/actions/runs/36761906537),
Linux concluiu bundle, AppImage e teste do ponto de entrada. Windows aprovou
44 testes, incluindo as duas provas Inno, mas falhou ao remover a pasta
temporária do beta smoke: o processo ainda mantinha o lease aberto.

A correção isola o cenário beta em um processo filho. O processo pai espera
o encerramento antes de remover a pasta temporária, inclusive no Windows.
O lock de segurança da aplicação **não foi removido nem liberado antecipadamente**.
Uma regressão verifica o encerramento e a limpeza efetiva. A falha do primeiro
runner permanece como evidência; seus binários não constituem a release final.
