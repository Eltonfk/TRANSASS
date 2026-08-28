# Demandas Pendentes

Registro de demandas identificadas pelo usuário, aguardando análise e implementação.

---

## D-001: Qualidade da tradução — palavras juntas e erros de português

**Status**: PENDENTE (registrada em 2026-08-28)
**Prioridade**: ALTA (afeta a qualidade percebida das legendas)

### Descrição

Algumas legendas apresentam:

1. **Palavras concatenadas** (sem espaço entre palavras):
   - Exemplo: `quemespalhou` em vez de `quem espalhou`
   - Frase observada: "Deve ser ele quemespalhou essa boate idiota."

2. **Erros de português** (homônimos/troca de palavras):
   - Exemplo: `boate` em vez de `boato`
   - Frase observada: "Deve ser ele quemespalhou essa boate idiota."
   - Correção esperada: "Deve ser ele quem espalhou esse boato idiota."

### Causa provável (análise preliminar)

- O modelo primário atual é **Qwen 3.5 9B** (Ollama local), um modelo de porte médio.
- **Palavras juntas**: o modelo pode omitir espaços ao gerar texto, especialmente com `temperature=0.0` e `num_predict` limitado. O Qwen 9B tem tendência a concatenar tokens em saídas longas.
- **Erros de homônimos**: "boate" vs "boato" é um erro semântico típico de modelos menores — o Qwen 9B pode confundir palavras foneticamente próximas.
- O Gemini (maior) tende a produzir menos desses erros, mas tem rate limit (429) que motivou o uso do Ollama como primário.

### Hipóteses a investigar (próxima sessão)

1. **Pós-processamento de normalização**: adicionar uma etapa de normalização que:
   - Detecte e corrija palavras concatenadas (ex: `quemespalhou` → `quem espalhou`);
   - Use um dicionário de homônimos comuns (ex: `boate` → `boato` quando o contexto indicar).
2. **Prompt**: reforçar no prompt a instrução de "preserve espaços entre palavras" e "revise homônimos".
3. **Modelo**: avaliar se o Gemini (quando o rate limit permitir) ou um modelo Ollama maior (ex: qwen3.5:32b) reduz esses erros.
4. **Validação pós-tradução**: adicionar um validador que detecte palavras concatenadas (regex de letras minúsculas seguidas de maiúsculas sem espaço) e marque para retry.

### Evidência

- Arquivo: `Paranoia Agent - S01E02 - The Golden Shoes WEBDL-1080p.pt-BR.ass`
- Frase: "Deve ser ele quemespalhou essa boate idiota."
- Modelo: `qwen3.5:9b` (Ollama), pipeline `v2_3_8`