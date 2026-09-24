# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O projeto
segue [versionamento semântico](https://semver.org/lang/pt-BR/).

## [0.1.0] - 2026-09-24

Primeira versão pública.

### O que há

- Servidor MCP local, só por stdio, com duas tools:
  - `consultar_protocolo(doenca_ou_condicao)`: PCDTs vigentes pelo nome da condição, com
    portaria, link do PDF completo e do PCDT resumido, seções extraídas e status quando o
    CSV de dados abertos tem.
  - `resumir_conduta(pcdt_id, contexto_clinico)`: resumo direcionado por LLM local
    (endpoint OpenAI-compatible), com citação literal conferida contra o texto do
    protocolo (`citacao_confere` e `fracao_citacao_verificada`).
- `pcdt-cli` com `sync`, `consultar`, `resumir`, `llm` e `schema`.
- Coleta da tabela de PCDTs da Conitec e do CSV de status, com até 20 PDFs por execução,
  cache de PDF em disco e índice FTS no DuckDB.
- Units systemd de usuário em `deploy/systemd/` para o sync diário.

### O que mudou antes da versão

- O identificador não carrega mais a nota de revisão do portal ("anexo alterado em
  ..."). A nota vai para o campo `nota_atualizacao`, e bases antigas são migradas no
  próximo sync, preservando o texto já extraído. `resumir_conduta` aceita o nome sem a
  nota.
- O status do CSV de dados abertos casa por nome sem acento, pontuação ou nota: de 37
  para 43 dos 132 PCDTs. Continua parcial.
- A busca por nome aceita as palavras em qualquer ordem, casa no começo da palavra, troca
  siglas comuns ("HAS", "DPOC", "DM2") pelo nome por extenso e acha "diabete melito" por
  "diabetes mellitus".
- Resultados que só citam o termo no texto vêm com `origem: "texto"`, no máximo 5, e com
  um aviso.
- O sync reextrai o texto quando o PDF, a portaria ou a nota de revisão mudam, em vez de
  manter o texto velho com o link novo.
- O sync não despromove ninguém quando a listagem traz menos de 80% dos PCDTs vigentes.
- A conferência de citação ignora "[...]", "(...)" e reticências nas pontas, que antes
  faziam uma citação real ser dada como não encontrada.
- O prompt do resumo fica dentro do pacote e entra no wheel.
- Removido o agendador interno (APScheduler, `SYNC_HORA_LOCAL`), que não era usado: o
  agendamento é o timer systemd.
- Piso do SDK MCP em `mcp>=2.2,<3`, porque o servidor usa `mcp.server.mcpserver`.
