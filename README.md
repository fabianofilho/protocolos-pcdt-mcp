# protocolos-pcdt-mcp

Servidor MCP local (stdio) que consulta os PCDTs (Protocolos Clínicos e Diretrizes
Terapêuticas) do Ministério da Saúde/Conitec e resume a conduta recomendada para um
contexto clínico específico, usando um LLM local. Toda resposta traz o link do PDF oficial.

O servidor roda só por stdio, na sua máquina, sem porta de rede. Não há transporte HTTP
nem connector remoto nesta versão, e expô-lo por túnel não é um uso suportado.

> ### ⚠️ Não substitui o protocolo nem o julgamento clínico
>
> - **Não é fonte oficial.** O projeto lê o que a Conitec publica e guarda uma cópia local,
>   que pode estar defasada em relação ao portal.
> - **O resumo é gerado por LLM e é uma ajuda de leitura.** Protocolos têm exceções,
>   populações específicas e notas de rodapé que um resumo de seis linhas não carrega.
> - **A citação literal é conferida, mas a interpretação não.** Ver
>   [Limitações conhecidas](#limitações-conhecidas): há um exemplo real de citação correta
>   com conclusão clínica errada.
> - **Sem validação clínica.** Não foi avaliado por nenhum órgão e não é dispositivo médico.
> - Para conduta, leia o protocolo completo. O link vem em toda resposta.

## Requisitos

| O quê | Versão | Para quê |
| --- | --- | --- |
| Python | 3.12+ | runtime |
| [uv](https://docs.astral.sh/uv/) | recente | dependências e venv |
| Um LLM local com API OpenAI-compatible | - | resumo direcionado |
| Espaço em disco | ~1 GB | base DuckDB + PDFs cacheados (protocolos são grandes) |

## Instalação

```bash
git clone https://github.com/fabianofilho/protocolos-pcdt-mcp.git
cd protocolos-pcdt-mcp
uv sync
cp .env.example .env
```

## Configuração

| Variável | Padrão | Observação |
| --- | --- | --- |
| `QWEN_ENDPOINT` | `http://127.0.0.1:8080/v1` | llama.cpp. Ollama: `:11434/v1`. LM Studio: `:1234/v1` |
| `QWEN_MODEL` | `local-model` | llama.cpp e LM Studio aceitam qualquer nome |
| `DUCKDB_PATH` | `./data/pcdt.duckdb` | base local |
| `COLETA_DELAY_SEGUNDOS` | `1` | intervalo entre downloads de PDF |

```bash
uv run pcdt-cli llm                 # confirma o LLM local
uv run pcdt-cli sync --max-pdfs 5   # teste rápido
uv run pcdt-cli sync                # coleta (20 PDFs por execução, por padrão)
uv run pcdt-cli consultar asma
uv run pcdt-cli resumir "asma" "paciente gestante"
```

A coleta baixa no máximo `--max-pdfs` protocolos por execução: são ~130 PDFs grandes, e a
ideia é a base completar ao longo de algumas noites em vez de sobrecarregar o portal numa
única. O texto já coletado é preservado entre execuções.

### Sync diário com systemd

O servidor MCP não agenda nada sozinho. O caminho oficial para manter a base atualizada é o
timer systemd de usuário versionado em [`deploy/systemd/`](deploy/systemd/): ele roda
`pcdt-cli sync` todo dia às 02:40, com até 30 min de atraso aleatório, e recupera o disparo
perdido se a máquina estava desligada.

As units supõem o clone em `~/protocolos-pcdt-mcp` e o `uv` em `~/.local/bin/uv`. Se os
seus caminhos forem outros, edite `WorkingDirectory` e `ExecStart` antes de copiar.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/protocolos-pcdt-sync.service deploy/systemd/protocolos-pcdt-sync.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now protocolos-pcdt-sync.timer
systemctl --user list-timers protocolos-pcdt-sync.timer   # próximo disparo
journalctl --user -u protocolos-pcdt-sync.service         # saída dos syncs
```

Para o timer rodar sem sessão aberta, habilite `loginctl enable-linger $USER`.

### Ligando ao Claude Code

```bash
claude mcp add protocolos-pcdt --scope user \
  -e DUCKDB_PATH=/caminho/para/protocolos-pcdt-mcp/data/pcdt.duckdb \
  -e QWEN_ENDPOINT=http://127.0.0.1:8080/v1 \
  -e QWEN_MODEL=local-model \
  -- uv --directory /caminho/para/protocolos-pcdt-mcp run protocolos-pcdt-mcp
```

## Uso

### `consultar_protocolo(doenca_ou_condicao: str)`

Busca primeiro no nome dos PCDTs vigentes:

- sem acento e sem caixa, com as palavras em qualquer ordem, sempre no começo de uma
  palavra ("asma" não casa com "citoplasma");
- trocando algumas siglas pelo nome por extenso (`HAS`, `DPOC`, `DM1`, `DM2`, `TDAH`,
  `LES`, `ELA`, `IC`, `DRC`, entre outras em `nomes.py`);
- aceitando "diabetes"/"diabete" e "mellitus"/"melito", porque a Conitec escreve
  "Diabete Melito Tipo 1".

Devolve até 10 protocolos pelo nome, com `origem: "nome"`. Se o nome não casar, procura o
termo no texto completo e devolve até 5 com `origem: "texto"` e um aviso: esses só citam
o termo e muitas vezes não são o PCDT da condição (por exemplo, "depressão" traz Alzheimer
e Parkinson, que mencionam depressão).

O `identificador` é o nome da condição em minúsculas. Quando o portal pendura uma nota de
revisão no nome, como "Asma (anexo alterado em 04/09/2026)", ela vai para
`nota_atualizacao` e fica fora do identificador. Saída real, da base local em 24/09/2026:

```json
{
  "termo": "acidentes ofídicos",
  "total": 1,
  "resultados": [
    {
      "identificador": "acidentes ofídicos",
      "condicao": "Acidentes Ofídicos",
      "status": "Conitec",
      "portaria": "Portaria SECTICS/MS nº 83 - 07/10/2025 (Publicada em 09/10/2025 )",
      "data_portaria": "2025-10-07",
      "nota_atualizacao": null,
      "url_pdf": "https://www.gov.br/conitec/pt-br/midias/protocolos/pcdt_acidentes_ofidicos_final.pdf/@@display-file/file",
      "url_resumido": "https://www.gov.br/conitec/pt-br/midias/protocolos/2026/pcdt-resumido/pcdt-resumido-acidentes-ofidicos/@@display-file/file",
      "vigente": true,
      "secoes_disponiveis": ["introducao", "diagnostico", "classificacao", "monitoramento", "tratamento"],
      "texto_completo_disponivel": true,
      "extracao_incompleta": false,
      "origem": "nome"
    }
  ],
  "aviso": null
}
```

`status` vem do CSV de dados abertos do Ministério da Saúde e é **parcial**: em 24/09/2026,
só 43 dos 132 PCDTs da listagem tinham status, porque boa parte dos nomes do CSV não
existe na tabela do portal. `status: null` não quer dizer que o protocolo não está
aprovado; a listagem da Conitec só traz PCDTs vigentes.

### `resumir_conduta(pcdt_id: str, contexto_clinico: str)`

Extrai do protocolo a parte que responde ao contexto, em vez de devolver o documento
inteiro. Aceita o `identificador` de `consultar_protocolo` ou o nome da condição sem a nota
de revisão (por exemplo, `asma`). Precisa do LLM local e do texto completo já coletado;
sem um dos dois, devolve só o link do PDF e um aviso dizendo o que faltou.

```json
{
  "condicao": "Acidentes Ofídicos",
  "resumo": {
    "resumo": "O paciente deve receber soroterapia antiveneno específica para o tipo de envenenamento.",
    "secao_origem": "1.3. Acesso a soroterapia antiveneno",
    "citacao_literal": "é necessário utilizar a soroterapia antiveneno específica, correspondente ao tipo de envenenamento",
    "citacao_confere": true,
    "fracao_citacao_verificada": 1.0
  },
  "url_pdf": "https://www.gov.br/conitec/..."
}
```

## A citação é conferida, não só pedida

O prompt exige a citação literal do trecho que sustenta o resumo. Um modelo pequeno às
vezes "cita" parafraseando, ou, pior, costura frases reais de partes diferentes do
documento num único bloco de aspas. Então o código confere:

| Campo | O que significa |
| --- | --- |
| `citacao_confere` | a citação existe inteira e **contígua** no protocolo |
| `fracao_citacao_verificada` | quanto dela existe, frase a frase (0 a 1) |

Fração alta com `citacao_confere: false` é o caso mais traiçoeiro: parece legítimo na
leitura e não é. Marcadores de omissão ("[...]", "(...)", reticências) nas pontas da
citação são ignorados; no meio, significam que algo foi pulado, e a citação deixa de
contar como contígua. Isso aconteceu no primeiro teste real deste projeto, com o PCDT de
Acidentes Ofídicos, e é por isso que os dois campos existem.

## Limitações conhecidas

**Conferir a citação não pega erro de interpretação.** Num teste com "paciente picado por
cascavel", o modelo local devolveu uma citação **real e contígua** do protocolo e mesmo
assim classificou o caso como envenenamento **botrópico**, quando cascavel é **crotálico**.
A citação estava certa; o raciocínio em cima dela, errado. Esta é a limitação mais
importante do projeto.

**O protocolo é truncado antes de ir para o modelo.** Protocolos passam de 200 mil
caracteres; o recorte prioriza a vizinhança das palavras do contexto perguntado, mas pode
cortar fora a parte relevante.

**A segmentação por seções é heurística.** A estrutura dos PCDTs varia entre protocolos
antigos e novos. Quando os títulos não são reconhecíveis, `secoes_disponiveis` vem vazio e
só o texto corrido fica disponível, de propósito, para não inventar estrutura.

**A base começa quase vazia.** Por causa do teto de PDFs por execução, os primeiros syncs
trazem a listagem completa mas pouco texto. `texto_completo_disponivel` diz quais já têm.
Na instância do mantenedor, em 24/09/2026, eram 64 de 132 com texto; no ritmo de 20 PDFs
por noite, a base deve completar em cerca de 4 syncs (estimativa). Até lá, PCDTs como
hipertensão arterial sistêmica e os de HIV aparecem na consulta, mas `resumir_conduta`
responde que o texto ainda não foi coletado. `pcdt-cli schema` mostra a contagem.

**Quando o PCDT muda, o texto antigo é descartado.** Se a URL do PDF, a data da portaria
ou a nota de revisão mudarem, o sync reextrai o texto, com prioridade na cota da noite. Se
não couber na cota, ou se o download falhar, o protocolo fica sem texto até um sync
conseguir baixar o PDF novo, em vez de resumir a versão velha com o link da nova. Quando
só a nota ou a portaria mudam e a URL continua a mesma, o PDF velho sai do cache em disco
na hora, para não ser reextraído depois.

**O status é parcial.** Ver a seção de `consultar_protocolo`.

**A busca no texto é um último recurso.** Ela acha protocolos que citam o termo, não o
PCDT da condição. Siglas fora da lista de `nomes.py` também caem nela.

**Listagem parcial não despromove ninguém.** Se a listagem do portal trouxer menos de 80%
dos PCDTs vigentes na base, o sync não tira ninguém de vigente e deixa um aviso no journal.

**PDFs digitalizados não têm camada de texto.** Nesses casos o registro fica com
`extracao_incompleta: true` e só os metadados.

**As URLs das fontes podem mudar.** Estão em `coleta/listagem.py`, confirmadas em
20/09/2026. Observação prática: os links `.csv` que o portal de dados abertos exibe estão
desatualizados e devolvem 403; os que funcionam terminam em `.zip`.

## Privacidade

- **Sai da máquina:** requisições ao `gov.br/conitec` e ao bucket de dados abertos do
  Ministério da Saúde, para a listagem e os PDFs públicos.
- **Não sai:** a condição e o contexto clínico que você consulta ficam entre a base local
  e o seu LLM local.
- Sem telemetria, sem analytics.

Atenção: o `contexto_clinico` que você digita vai para o seu LLM. Se ele estiver
hospedado fora da sua máquina, o texto vai junto, este projeto não impede isso, ao
contrário do `revisor-notas-mcp`.

## Atualizando de uma base anterior

A versão 0.1.0 tira a nota de revisão do identificador e cria a coluna
`nota_atualizacao`. A migração roda no próximo `pcdt-cli sync` (ou em qualquer abertura
da base para escrita) e preserva o texto já extraído. Até lá, o servidor continua lendo a
base antiga: os identificadores aparecem com a nota, e `resumir_conduta` aceita o nome
sem ela.

## Segurança

Ver [SECURITY.md](SECURITY.md). Mudanças por versão em [CHANGELOG.md](CHANGELOG.md).

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md). Não rode a coleta em loop contra o portal.

## Licença e atribuição

[Apache License 2.0](LICENSE): escolhida por o projeto tocar em conduta clínica.

Construído no contexto do [IA.med](https://iamed.cc).
