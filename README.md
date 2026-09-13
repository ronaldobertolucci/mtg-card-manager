# MTG Card Manager

Microsserviço FastAPI que mantém um espelho local do Scryfall Oracle Cards e aplica
traduções customizadas sem alterá-las durante o sincronismo oficial.

## Executar

```bash
docker compose up --build -d mtg-card-manager-api mtg-card-manager-db
docker compose --profile sync run --rm mtg-card-manager-sync
```

Ao atualizar uma instalação de desenvolvimento criada antes da correção do
`oracle_id`, recrie o banco local uma vez:

```bash
docker compose down --volumes --remove-orphans
docker compose up --build -d mtg-card-manager-api mtg-card-manager-db
docker compose --profile sync run --rm mtg-card-manager-sync
```

A documentação interativa fica em `http://localhost:8000/docs`.

Exemplos de busca:

```bash
curl 'http://localhost:8000/cards/search?lang=pt-BR&name=raio&colors=R'
curl 'http://localhost:8000/cards/search?lang=en&type_line=creature&cmc_gte=2&cmc_lte=4'
```

Filtros textuais usam correspondência parcial sem diferenciar maiúsculas/minúsculas.
`colors` representa igualdade exata do array de cores na ordem informada. `limit` aceita
de 1 a 200 itens (padrão 50), e `offset` permite paginação.

Quando `lang` for diferente de `en`, somente cartas com uma tradução cadastrada nesse
idioma serão retornadas. A API não mistura texto oficial em inglês com uma resposta
localizada; se nenhuma tradução corresponder à busca, retorna HTTP 404.

## Traduções

O CRUD de traduções expõe os seguintes endpoints:

```text
POST   /translations
GET    /translations?oracle_id=...&lang=pt-BR&limit=50&offset=0
GET    /translations/{translation_id}
GET    /translations/by-card/{oracle_id}/{lang}
PATCH  /translations/{translation_id}
DELETE /translations/{translation_id}
```

`oracle_id` deve corresponder ao campo `oracle_id` devolvido por `/cards/search`. Ele
representa a identidade mecânica estável da carta no Scryfall; o campo `id` identifica
somente a impressão atualmente usada como representante no Oracle Bulk. Não é
permitido cadastrar traduções `en`, pois o conteúdo oficial em inglês pertence a
`oracle_cards`. Criações duplicadas para o mesmo par `(oracle_id, lang)` retornam HTTP
409.

## Sincronização diária

O serviço `mtg-card-manager-sync` é deliberadamente isolado da API. Ele obtém o
endereço atual do Bulk Data, baixa para um arquivo temporário, descompacta e processa
o JSONL em streaming com `ijson`. Em seguida, executa
`UpdateOne(..., upsert=True)` em lotes com `ordered=False`. O leitor também aceita o
formato legado de array JSON.

O sincronizador usa `oracle_id` como `_id` e chave do upsert em `oracle_cards`. O campo
`id` do Scryfall permanece no documento apenas como identificador da impressão atual.
Se o Scryfall trocar a impressão representante, o mesmo documento é atualizado, sem
criar uma segunda carta com o mesmo `oracle_id`. Um índice único em `oracle_id` reforça
essa invariante no MongoDB.

As requisições ao Scryfall usam timeouts separados de conexão e leitura e fazem até
quatro tentativas com backoff exponencial para timeouts, falhas de conexão, HTTP 429 e
erros HTTP 5xx. Downloads interrompidos são descartados e reiniciados na tentativa
seguinte.

Para testar com um arquivo já baixado:

```bash
python sync_scryfall.py --file oracle-cards.jsonl.gz --batch-size 1000
```

Agende o comando de sincronização uma vez por dia no scheduler da infraestrutura
(cron, Kubernetes CronJob, GitHub Actions self-hosted etc.). A coleção `translations`
nunca é tocada pelo sincronizador. O índice único `(oracle_id, lang)` é criado no
startup da API.

Documento de tradução de exemplo:

```json
{
  "oracle_id": "oracle-id-da-carta-no-scryfall",
  "lang": "pt-BR",
  "name": "Nome traduzido",
  "oracle_text": "Texto traduzido",
  "type_line": "Linha de tipo traduzida",
  "flavor_text": "Texto de ambientação"
}
```

## Desenvolvimento

```bash
python -m pip install -e '.[dev]'
pytest
```

Parâmetros inválidos ou conflitantes retornam HTTP 422 pela validação nativa do
FastAPI/Pydantic. O handler converte falhas de validação de query para HTTP 400 para
cumprir o contrato da API.
