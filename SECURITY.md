# Segurança

## Como reportar

Não descreva a vulnerabilidade numa issue pública. Abra uma
[issue](https://github.com/fabianofilho/protocolos-pcdt-mcp/issues/new) só com o título
"Contato de segurança", sem detalhes, e o mantenedor responde combinando um canal
privado. Depois, mande o problema, a versão ou o commit, e como reproduzir. Projeto
mantido por uma pessoa: a resposta costuma sair em alguns dias.

## Escopo

Vale para o código deste repositório: o servidor MCP (stdio), o `pcdt-cli`, a coleta do
portal da Conitec e as units em `deploy/systemd/`. Exemplos do que interessa: leitura ou
escrita fora de `DUCKDB_PATH` e do cache de PDFs, execução de código a partir de um PDF
ou de uma resposta do portal, e dados da consulta saindo da máquina sem estar
documentado.

Fora do escopo: o conteúdo clínico dos PCDTs (reporte à Conitec), o LLM local que você
configurar e a disponibilidade dos portais do governo.

O servidor foi feito para rodar localmente por stdio, sem porta de rede. Expor o servidor
por HTTP ou por um túnel não é um uso suportado nesta versão.
