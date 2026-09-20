# protocolos-pcdt-mcp

Servidor MCP que consulta os PCDTs (Protocolos Clínicos e Diretrizes Terapêuticas)
vigentes do Ministério da Saúde/Conitec, e resume a conduta direcionada a um contexto
clínico usando o LLM local.

## As fontes

Confirmadas em 20/09/2026 abrindo as páginas — nenhuma foi deduzida:

| Fonte | O que dá | Observação |
| --- | --- | --- |
| [Tabela de PCDTs da Conitec](https://www.gov.br/conitec/pt-br/assuntos/avaliacao-de-tecnologias-em-saude/protocolos-clinicos-e-diretrizes-terapeuticas/pcdt) | Condição, portaria, PDF completo e PCDT resumido | Única com os PDFs; ~150 linhas |
| CSV de dados abertos do MS | Nome e status de cada PCDT | Descoberto na pesquisa; a spec não o previa |

O CSV vive em `s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CONITEC/csv/pcdt.csv.zip` —
os links `.csv` que o portal exibe estão desatualizados e devolvem 403; os que funcionam
terminam em `.zip`.

## Rodando

```bash
uv sync
cp .env.example .env
uv run pcdt-cli llm                 # confirma o LLM local
uv run pcdt-cli sync --max-pdfs 5   # teste rápido
uv run pcdt-cli sync                # coleta (20 PDFs por execução, por padrão)
uv run pcdt-cli consultar asma
uv run pcdt-cli resumir "asma" "paciente gestante"
uv run protocolos-pcdt-mcp          # servidor MCP no stdio
```

A coleta baixa no máximo `--max-pdfs` protocolos por execução: são ~150 PDFs grandes, e a
ideia é a base completar ao longo de algumas noites em vez de sobrecarregar o portal numa
única. O texto já coletado é preservado entre execuções.

## Tools

### `consultar_protocolo(doenca_ou_condicao)`
Busca pelo nome da condição; se não achar, procura no texto completo — uma condição pode
ser tratada dentro do PCDT de outra. Devolve todos os protocolos relacionados, cada um com
o link do PDF oficial e as seções extraídas.

### `resumir_conduta(pcdt_id, contexto_clinico)`
Extrai do protocolo a parte que responde ao contexto ("paciente com contraindicação a
metformina"), em vez de devolver o documento inteiro.

## A citação literal é conferida, não só pedida

O prompt exige a citação literal do trecho que sustenta o resumo. Um modelo pequeno às
vezes "cita" parafraseando, então o código **confere se a citação existe mesmo no texto do
protocolo** e devolve `citacao_confere: true/false`. Quando é falso, o aviso diz para
tratar com desconfiança.

Isso transforma uma promessa de prompt em um fato verificável — que é a diferença entre
uma ajuda de leitura e uma alucinação com aparência de citação.

A resposta traz dois campos:

- `citacao_confere` — a citação existe inteira e **contígua** no protocolo;
- `fracao_citacao_verificada` — quanto dela existe, frase a frase.

Fração alta com `citacao_confere: false` é o caso mais traiçoeiro: o modelo costurou
frases reais de partes diferentes do documento num único bloco de aspas. Parece legítimo
na leitura e não é. Isso aconteceu de verdade no primeiro teste com o PCDT de Acidentes
Ofídicos, e é por isso que os dois campos existem.

## O que a conferência NÃO pega

Testando com "paciente picado por cascavel", o Qwen local devolveu uma citação **real e
contígua** do protocolo — e mesmo assim classificou o caso como envenenamento
**botrópico**, quando cascavel é **crotálico**. A citação estava certa; o raciocínio em
cima dela, errado.

Conferir a citação elimina a alucinação de fonte, não o erro de interpretação. Por isso a
tool é uma ajuda de leitura, e o link do PDF vem em toda resposta.

## O resumo não substitui o protocolo

Protocolos mudam, têm exceções e notas de rodapé que um resumo de seis linhas não carrega.
Um resumo com citação conferida é um bom ponto de partida para abrir o documento, nunca
um substituto dele.
