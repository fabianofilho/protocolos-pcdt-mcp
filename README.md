# protocolos-pcdt-mcp

Servidor MCP que consulta os PCDTs (Protocolos Clínicos e Diretrizes Terapêuticas) do
Ministério da Saúde/Conitec e resume a conduta recomendada para um contexto clínico
específico, usando um LLM local. Toda resposta traz o link do PDF oficial.

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

Busca pelo nome da condição; se não achar, procura no texto completo, uma condição pode
ser tratada dentro do PCDT de outra. Devolve todos os protocolos relacionados.

```json
{
  "termo": "acidentes ofídicos",
  "total": 1,
  "resultados": [
    {
      "identificador": "acidentes ofídicos",
      "condicao": "Acidentes Ofídicos",
      "status": "Conitec",
      "portaria": "Portaria SECTICS/MS nº 83 - 07/10/2025",
      "url_pdf": "https://www.gov.br/conitec/pt-br/midias/protocolos/pcdt_acidentes_ofidicos_final.pdf/@@display-file/file",
      "secoes_disponiveis": ["introducao", "classificacao", "diagnostico", "tratamento", "monitoramento"],
      "texto_completo_disponivel": true
    }
  ]
}
```

### `resumir_conduta(pcdt_id: str, contexto_clinico: str)`

Extrai do protocolo a parte que responde ao contexto, em vez de devolver o documento
inteiro.

```json
{
  "condicao": "Acidentes Ofídicos",
  "resumo": {
    "resumo": "O paciente deve receber soroterapia antiveneno específica para o tipo de envenenamento.",
    "secao_origem": "1.3. Acesso a soroterapia antiveneno",
    "citacao_literal": "…é necessário utilizar a soroterapia antiveneno específica, correspondente ao tipo de envenenamento…",
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
leitura e não é. Isso aconteceu no primeiro teste real deste projeto, com o PCDT de
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

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md). Não rode a coleta em loop contra o portal.

## Licença e atribuição

[Apache License 2.0](LICENSE): escolhida por o projeto tocar em conduta clínica.

Construído no contexto do [IA.med](https://iamed.cc).
