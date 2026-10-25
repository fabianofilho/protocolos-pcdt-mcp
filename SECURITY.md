# Segurança

## Como reportar

Não descreva a vulnerabilidade numa issue pública. Abra uma
[issue](https://github.com/fabianofilho/protocolos-pcdt-mcp/issues/new) só com o título
"Contato de segurança", sem detalhes, e o mantenedor responde combinando um canal
privado. Depois, mande o problema, a versão ou o commit, e como reproduzir. Projeto
mantido por uma pessoa: a resposta costuma sair em alguns dias.

## Escopo

Vale para o código deste repositório: o servidor MCP (stdio e modo connector HTTP), o
`pcdt-cli`, a coleta do
portal da Conitec e as units em `deploy/systemd/`. Exemplos do que interessa: leitura ou
escrita fora de `DUCKDB_PATH` e do cache de PDFs, execução de código a partir de um PDF
ou de uma resposta do portal, e dados da consulta saindo da máquina sem estar
documentado.

Fora do escopo: o conteúdo clínico dos PCDTs (reporte à Conitec), o LLM local que você
configurar e a disponibilidade dos portais do governo.

Por padrão o servidor roda localmente por stdio, sem porta de rede. No modo connector
(`TRANSPORTE=streamable-http`) ele escuta em HTTP, sem autenticação, com teto de
requisições por minuto, validação do cabeçalho `Host` e somente leitura da base; nesse
modo o LLM não é chamado. Interessa, por exemplo, contornar os tetos ou a validação de
`Host`, ou fazer o servidor HTTP escrever na base ou chamar o LLM. Ataque distribuído de
negação de serviço fica fora do escopo.
