# MTG Card Manager

Microsserviço em Python e FastAPI para ingerir, armazenar e pesquisar dados de cartas
de Magic: The Gathering. O serviço mantém uma cópia local do Scryfall Oracle Cards e
permite cadastrar traduções próprias, principalmente em `pt`, sem sobrescrever
traduções durante o sincronismo diário. Pesquisas usam a base local; a resolução de
IDs consulta o Scryfall em tempo real somente para impressões ausentes dessa base.

## Tecnologias

- Python 3.11+
- FastAPI e Pydantic
- MongoDB
- Motor para acesso assíncrono da API
- PyMongo para o sincronizador isolado
- `ijson` para leitura incremental do Bulk Data
- Docker Compose

## Modelo de dados e identidade das cartas

O banco possui duas coleções independentes.

### `oracle_cards`

Contém os dados oficiais recebidos do Scryfall. A identidade estável é o `oracle_id`:

```json
{
  "_id": "oracle-id-estavel",
  "oracle_id": "oracle-id-estavel",
  "id": "id-da-impressao-representante-atual",
  "name": "Lightning Bolt",
  "oracle_text": "Lightning Bolt deals 3 damage to any target.",
  "type_line": "Instant",
  "colors": ["R"],
  "mana_cost": "{R}",
  "cmc": 1
}
```

- `_id` recebe o `oracle_id`.
- `oracle_id` identifica permanentemente a carta e é único na coleção.
- `id` identifica apenas a impressão atualmente escolhida pelo Scryfall como
  representante daquele `oracle_id`.
- Quando a impressão representante muda, o sincronismo atualiza o campo `id` do mesmo
  documento.

O índice único `uq_oracle_cards_oracle_id` impede que dois documentos tenham o mesmo
`oracle_id`.

### `translations`

Armazena somente traduções customizadas:

```json
{
  "_id": "ObjectId gerado pelo MongoDB",
  "oracle_id": "oracle-id-estavel",
  "lang": "pt",
  "name": "Raio",
  "oracle_text": "Raio causa 3 pontos de dano a qualquer alvo.",
  "type_line": "Mágica Instantânea",
  "flavor_text": null,
  "created_at": "2026-09-13T12:00:00Z",
  "updated_at": "2026-09-13T12:00:00Z"
}
```

O campo `oracle_id` referencia a identidade estável de `oracle_cards`. O índice único
composto `uq_translation_oracle_lang` permite somente uma tradução para cada par
`(oracle_id, lang)`.

## Execução com Docker

### Pré-requisitos

- Docker com o plugin Docker Compose
- Acesso à internet para baixar o Bulk Data do Scryfall

### Iniciar API e MongoDB

```bash
docker compose up --build -d mtg-card-manager-api mtg-card-manager-db
```

Serviços e recursos criados:

| Recurso | Nome |
| --- | --- |
| API | `mtg-card-manager-api` |
| MongoDB | `mtg-card-manager-db` |
| Sincronizador | `mtg-card-manager-sync` |
| Volume | `mtg-card-manager-db-data` |
| Rede | `mtg-card-manager-network` |

A API fica disponível em `http://localhost:8002`.

- Swagger UI: `http://localhost:8002/docs`
- OpenAPI: `http://localhost:8002/openapi.json`
- Health check: `GET http://localhost:8002/health`

### Executar a sincronização manualmente

```bash
docker compose --profile sync run --rm mtg-card-manager-sync
```

O sincronizador não é executado automaticamente ao subir a API. Isso permite executá-lo
manualmente, por um `cron`, por um Kubernetes CronJob ou pelo agendador da infraestrutura.

### Recriar o banco de desenvolvimento

Como não existem migrações, bancos criados com versões antigas do modelo devem ser
recriados:

```bash
docker compose down --volumes --remove-orphans
docker compose up --build -d mtg-card-manager-api mtg-card-manager-db
docker compose --profile sync run --rm mtg-card-manager-sync
```

> O comando `docker compose down --volumes` apaga definitivamente os dados locais do
> MongoDB, incluindo traduções cadastradas.

## Configuração

As principais variáveis estão documentadas em `.env.example`:

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `MONGODB_URI` | `mongodb://localhost:27017` no código | Conexão com o MongoDB. No Compose é usado `mongodb://mtg-card-manager-db:27017`. |
| `MONGODB_DATABASE` | `card_manager` | Nome do banco. |
| `SCRYFALL_BULK_METADATA_URL` | endpoint Oracle Cards | Endpoint que informa a URL atual do Bulk Data. |
| `SCRYFALL_USER_AGENT` | identificação do projeto | `User-Agent` enviado ao Scryfall. |
| `SYNC_BATCH_SIZE` | `1000` | Quantidade de operações em cada `bulk_write`. |

## Sincronização com o Scryfall

O arquivo `sync_scryfall.py`:

1. Consulta os metadados de Oracle Cards.
2. Obtém `jsonl_download_uri` ou, para compatibilidade, `download_uri`.
3. Baixa o arquivo para um diretório temporário.
4. Detecta automaticamente JSON, JSONL e conteúdo compactado com gzip.
5. Processa uma carta por vez com `ijson`, sem carregar o arquivo inteiro na memória.
6. Executa `UpdateOne(..., upsert=True)` em lotes e com `ordered=False`.

O filtro do upsert é o `oracle_id`:

```python
UpdateOne(
    {"oracle_id": oracle_id},
    {
        "$set": card,
        "$setOnInsert": {"_id": oracle_id},
    },
    upsert=True,
)
```

Uma mudança no `id` da impressão atualiza o documento existente. Cards sem `id` ou
sem `oracle_id` não são importados. A coleção `translations` nunca é alterada pelo
sincronizador.

Falhas de conexão, timeouts, HTTP 429 e erros HTTP 5xx elegíveis são repetidos até
quatro vezes com espera exponencial. Downloads interrompidos são descartados antes da
próxima tentativa.

Também é possível sincronizar um arquivo já baixado:

```bash
python sync_scryfall.py --file oracle-cards.jsonl.gz --batch-size 1000
```

Ao executar o script fora do Docker, configure uma URI acessível pelo host:

```bash
MONGODB_URI=mongodb://localhost:27017 python sync_scryfall.py
```

## API

### Acesso público e contrato de cartas

**A leitura do catálogo é pública e não exige autenticação.** Isso inclui a busca,
a consulta individual ou em lote por Oracle ID e a resolução de IDs de impressões. O login da interface
é uma decisão do frontend e não restringe esses endpoints. A política de autorização
para escrita de traduções é separada; esta entrega não adiciona autenticação ao CRUD.

Busca e consulta individual usam o mesmo `CardResponse`, com modelos explícitos para
`CardFaceResponse` e `CardImages` no OpenAPI. Os campos declarados aparecem sempre na
resposta, inclusive quando seu valor é `null`. Campos não declarados da fonte deixam
de ser repassados automaticamente ao cliente.

| Campo | Contrato |
| --- | --- |
| `id` / `oracle_id` | ID da impressão representante / identidade estável da carta. |
| `lang` | Idioma dos textos retornados. |
| `printing_lang` | Idioma original da impressão, preservado antes da tradução; `null` se desconhecido. Não é substituído pelo idioma solicitado. |
| `layout` | Identificador de layout da fonte; `null` se indisponível. Aceita novos identificadores sem limitar a enumeração. |
| `colors` | Cores no nível principal: `null` se indisponíveis nesse nível; `[]` se comprovadamente incolor. Em multiface, consulte também as faces. |
| `color_identity` | Identidade da carta inteira: `null` se desconhecida; `[]` para identidade incolor. |
| `color_indicator` | Indicador de cor informado pela fonte, ou `null` se não informado. |
| `image_uris` | Objeto com `small`, `normal`, `large`, `png`, `art_crop` e `border_crop`; variantes indisponíveis são `null`. O objeto inteiro é `null` se não há imagens nesse nível. |
| `card_faces` | Lista em ordem original; `[]` quando não há faces fornecidas. Cada face tem textos, atributos mecânicos e imagens próprios tipados. |
| `loyalty` / `defense` | Texto ou `null`, assim como `power` e `toughness`; valores especiais não são convertidos em números. |
| `legalities` | Mapa de status por formato; `{}` quando ausente, sem inferir legalidade. |
| `keywords` | Palavras-chave oficiais, preservadas em todos os idiomas; `[]` quando ausentes ou `null` na fonte. Usadas, por exemplo, na validação de Companion. |
| `produced_mana` | Símbolos de mana produzida, incluindo `C` e símbolos especiais; `[]` quando ausentes ou `null` na fonte. |
| `rarity` | Raridade da impressão representante, ou `null` quando indisponível. |
| `all_parts` | Relações oficiais com outras impressões: `id` obrigatório e `component`, `name`, `type_line`, `uri` opcionais (`null` quando ausentes). Os textos dessas relações não são traduzidos. Lista ausente ou `null` na fonte retorna `[]`. |

`mana_cost: ""` é preservado e difere de `null` (não informado).
Campos textuais opcionais e CMC ausentes continuam `null`. Textos opcionais sem
tradução também são `null`, sem mistura automática com inglês. Imagens continuam
vinculadas à impressão original, podendo estar em outro idioma.

Os erros documentados preservam o comportamento existente: validação da busca é
`400` com `detail` em lista; validação de corpo/caminho nas demais rotas é `422`.
Erros de domínio usam `detail` textual. No CRUD, `422` também pode trazer texto para
estrutura de tradução inválida. O OpenAPI inclui os erros específicos de cada rota,
inclusive `409` de tradução duplicada e `502` na resolução via Scryfall.

A busca retorna um objeto paginado com `items`, `limit`, `offset` e `hasNext`.
Filtros são opcionais, e páginas vazias retornam `200` com `items: []` e `hasNext: false`.

### Consultar cartas em lote por Oracle ID

`POST /cards/batch` é uma operação pública de leitura. Retorna os dados completos
do DTO de cartas a partir do catálogo local, sem uma requisição por carta e sem
consultar o Scryfall em tempo real.

```bash
curl -X POST http://localhost:8002/cards/batch \
  -H 'Content-Type: application/json' \
  -d '{"oracleIds": ["oracle-id-1", "oracle-id-2"], "lang": "pt", "fallbackLang": "en"}'
```

| Campo de entrada | Regra |
| --- | --- |
| `oracleIds` | Obrigatório; lista de até 200 posições, antes da deduplicação. Cada ID aceita letras, números e hífens, com 1 a 100 caracteres. Lista vazia é válida. |
| `lang` | Opcional, padrão `pt`. Normaliza o idioma como no CRUD de traduções: `PT` → `pt`, `pt-br` → `pt-BR`. |
| `fallbackLang` | Opcional; somente `"en"` ou `null`. Ausente ou `null` desabilita fallback. Com `lang=en`, não altera a resposta. |

Campos desconhecidos são rejeitados. A lista é deduplicada pelo ID exato, mantendo
a ordem da primeira ocorrência em cada lista de saída. A quantidade de cópias de
uma carta continua sendo responsabilidade do deck, não do catálogo.

Exemplo de resposta com campos de cartas abreviados:

```json
{
  "cards": [
    {
      "oracle_id": "oracle-id-1",
      "id": "printing-id-1",
      "name": "Lightning Bolt",
      "lang": "en",
      "printing_lang": "en",
      "requested_lang": "pt",
      "fallback_reason": "translation_missing"
    }
  ],
  "missing": [
    {"oracle_id": "oracle-id-2", "reason": "card_not_found"}
  ]
}
```

Cada elemento de `cards` contém o DTO completo, incluindo faces, imagens, `layout`,
identidade de cor e legalidades, mais dois campos:

- `requested_lang`: idioma solicitado, em formato canônico.
- `fallback_reason`: `null` quando o idioma solicitado foi atendido;
  `translation_missing` ou `translation_invalid` quando houve fallback.

`lang` informa o idioma dos textos efetivamente retornados. `printing_lang` preserva
o idioma da impressão e pode diferir dos textos. O fallback usa o documento oficial
inteiro em inglês, incluindo todas as faces, sem misturar traduções parciais.

| Motivo em `missing` | Significado |
| --- | --- |
| `card_not_found` | Oracle ID ausente do catálogo local; não afirma inexistência no Scryfall. |
| `translation_missing` | Carta local existe, mas não há tradução no idioma solicitado. |
| `translation_invalid` | Tradução existe, mas é estruturalmente inválida, por exemplo com faces incompletas. |

Sem fallback, traduções indisponíveis entram em `missing`. Com fallback, essas cartas
entram em `cards` com `lang=en`, `requested_lang` e `fallback_reason`, e não se repetem
em `missing`. Cartas ausentes do catálogo local continuam em `missing`.

Lotes parcialmente ou totalmente ausentes retornam `200`. Uma lista vazia retorna
`{"cards": [], "missing": []}`. Corpo, ID, idioma ou limite inválido retorna `422`
com `detail` em lista, sem executar parte da consulta. Erros de infraestrutura não
são classificados como ausências de cartas ou traduções.

A consulta realiza uma busca em lote por `oracle_id` e, quando necessário, outra
para traduções no idioma solicitado. Não aplica filtros de busca, de legalidade ou
a exclusão padrão de tokens: hidrata todas as entradas disponíveis que o deck enviou.
O frontend deve preservar as entradas e quantidades do deck, associando resultados
por `oracle_id`; pode exibir “Tradução indisponível — exibindo inglês” quando houver
fallback. A busca e a consulta individual continuam exigindo tradução válida sem
fallback implícito. `/cards/resolve` mantém seu contrato próprio de IDs de impressão.

### Resolver IDs de impressões

`POST /cards/resolve` recebe IDs de impressões do Scryfall e retorna seus Oracle IDs:

```bash
curl -X POST http://localhost:8002/cards/resolve \
  -H 'Content-Type: application/json' \
  -d '{"ids": ["scryfall-id-1", "scryfall-id-2"]}'
```

Exemplo ilustrativo de resposta:

```json
[
  {
    "id": "scryfall-id-1",
    "oracleId": "oracle-id-1",
    "name": "Lightning Bolt",
    "layout": "normal",
    "typeLine": "Instant"
  },
  {
    "id": "scryfall-id-2",
    "oracleId": "oracle-id-1",
    "name": "Lightning Bolt",
    "layout": "normal",
    "typeLine": "Instant"
  }
]
```

A consulta local filtra `oracle_cards.id` em lote, usando o índice `ix_oracle_cards_id`.
Cada ID ausente é consultado via `GET https://api.scryfall.com/cards/{id}`.
O retorno preserva a ordem e as repetições da entrada; IDs repetidos são consultados
uma única vez por chamada. Uma lista vazia retorna `[]`. Os dados do fallback não são
persistidos e não recebem traduções customizadas.

Payload inválido retorna `422`. Se algum ID não existir no Scryfall, a operação retorna
`404`; falhas HTTP, timeout ou resposta externa incompatível com o DTO retornam `502`.
Esses erros interrompem a operação, sem retornar uma lista parcial. O cliente HTTP
usa timeout de 10 segundos e intervalo de 100 ms entre consultas externas do mesmo lote.

Todas as respostas são JSON, exceto exclusões bem-sucedidas, que retornam corpo vazio.

### Health check

```http
GET /health
```

Resposta `200 OK`:

```json
{
  "status": "ok"
}
```

## Busca de cards

```http
GET /cards/search
```

Os filtros são opcionais. Quando informados, são combinados com lógica `AND`.
Sem filtros, `lang=en` abre o catálogo oficial; o padrão `lang=pt` abre somente
as traduções válidas. Não é necessário usar `cmc_gte=0`, e cartas sem CMC informado
continuam elegíveis. Sem `kind`, layouts não pertencentes a decks e cartas oversized
são excluídos por padrão.

```http
GET /cards/search?lang=en&limit=50&offset=0
GET /cards/search?lang=pt&limit=50&offset=0
```

### Parâmetros

| Parâmetro | Tipo | Padrão | Comportamento |
| --- | --- | --- | --- |
| `lang` | string | `pt` | Idioma da resposta. Use `en` para dados oficiais ou um idioma com traduções cadastradas. |
| `name` | string | — | Busca parcial por nome, sem diferenciar maiúsculas e minúsculas. |
| `name_exact` | string | — | Busca pelo nome completo com igualdade (`=`), diferenciando maiúsculas e minúsculas. |
| `kind` | string | — | `cards`, `accessories` ou `all`; classificação nos dados oficiais, independente do idioma. |
| `include_tokens` | boolean | omitido: `false` | Obsoleto. Sem `kind`, `true` inclui componentes; omitido ou `false` aplica a limpeza física padrão. Não combine com `kind`. |
| `oracle_text` | string | — | Busca parcial no texto Oracle. |
| `type_line` | string | — | Busca parcial na linha de tipo. |
| `colors` | string | — | Cores separadas por vírgula. Aceita somente `W,U,B,R,G`, sem repetição e sem depender da ordem. |
| `colors_mode` | string | omitido: `exact` | Operador `any`, `all` ou `exact`; exige `colors`. |
| `colorless` | boolean | — | `true`: cores conhecidas e vazias; `false`: cores conhecidas e não vazias. Não pode ser combinado com `colors` ou `colors_mode`. |
| `color_identity` | string | — | Identidade da carta inteira, com cores `W,U,B,R,G` separadas por vírgula, sem repetição. |
| `color_identity_mode` | string | omitido: `exact` | `any`, `all`, `exact` ou `subset`; exige `color_identity`. |
| `identity_colorless` | boolean | — | `true`: identidade conhecida vazia; `false`: identidade conhecida não vazia. Incompatível com `color_identity` e `color_identity_mode`. |
| `mana_cost` | string | — | Correspondência exata do custo, por exemplo `{R}` ou `{1}{U}`. |
| `cmc` | número | — | Valor de mana exato, maior ou igual a zero. |
| `cmc_gte` | número | — | Valor de mana mínimo, inclusivo. |
| `cmc_lte` | número | — | Valor de mana máximo, inclusivo. |
| `power` | string | — | Poder exato, incluindo valores não numéricos como `*`. |
| `toughness` | string | — | Resistência exata. |
| `format` | lista de strings | — | Identificadores dinâmicos separados por vírgula ou parâmetros repetidos (OR); sozinho seleciona `legal,restricted`. |
| `legality` | string | `legal,restricted` com formato | Status separados por vírgula; exige `format`. |
| `is_commander` | boolean | — | `true`: elegível como comandante independente e legal em Commander; `false`: complemento; omitido: não filtra elegibilidade. |
| `limit` | inteiro | `50` | Quantidade de resultados, entre 1 e 200. |
| `offset` | inteiro | `0` | Quantidade de cartas válidas ignoradas para paginação, entre 0 e 100.000. |

Regras importantes:

- `cmc` não pode ser combinado com `cmc_gte` ou `cmc_lte`.
- `cmc_gte` não pode ser maior que `cmc_lte`.
- Sem `colors_mode`, `colors` exige exatamente as cores informadas, sem depender da
  ordem. Para “azul ou vermelho”, use `colors=U,R&colors_mode=any`.
- `colors_mode` sem `colors`, valores inválidos ou combinações com `colorless`
  retornam `400`. Cores duplicadas e componentes vazios, como `U,,R`, também são inválidos.
- Para custos com chaves, faça URL encoding quando necessário: `{R}` vira `%7BR%7D`.
- Recomenda-se usar o formato canônico dos idiomas, como `en` e `pt`.

### Cartas e acessórios

Use `kind=cards|accessories|all` para selecionar a categoria:

| Valor | Seleção |
| --- | --- |
| `cards` | Aplica a limpeza física padrão e exclui a categoria de acessórios. |
| `accessories` | Somente acessórios: tokens, tokens de duas faces, emblemas e tipos oficiais contendo `Dungeon`. |
| `all` | Sem restrição por categoria; inclui cartas e acessórios. Os demais filtros continuam valendo. |

A classificação usa exclusivamente os campos **principais oficiais** em `oracle_cards`:
layout igual a `token`, `double_faced_token` ou `emblem`, ou `type_line` contendo a
substring literal `Dungeon`, diferenciando maiúsculas de minúsculas. O tipo traduzido
e os tipos isolados das faces não alteram a categoria.
A limpeza física exclui `token`, `double_faced_token`, `emblem`, `art_series`,
`planar`, `scheme` e `vanguard`, além de `oversized=true`. Layouts de
cartas reais, incluindo `transform`, `modal_dfc`, `split` e `reversible_card`,
continuam elegíveis. Metadados ausentes não causam exclusão. Essa regra usa os
dados oficiais do Scryfall, sem consultar formatos ou regras de clientes.

`kind` é aplicado antes da paginação e combina com os demais filtros por `AND`, sem
alterar a correspondência na mesma face. Em português, acessórios ainda precisam de
tradução válida para aparecer na busca; não há fallback automático.

```http
GET /cards/search?lang=en&kind=cards
GET /cards/search?lang=en&kind=accessories
GET /cards/search?lang=pt&kind=accessories
GET /cards/search?lang=en&kind=all&name_exact=Ornithopter
```

**Transição de `include_tokens`:** o parâmetro está marcado como obsoleto no OpenAPI.
Sem `kind`, omitido ou `false` aplica a limpeza física padrão; `true` não aplica
essa limpeza. `kind=all` também permite acesso explícito aos componentes.
`kind=cards` acrescenta a exclusão da categoria de acessórios por `type_line`.

Enviar `kind` junto de `include_tokens` retorna `400`, mesmo com `include_tokens=false`
ou com valores aparentemente equivalentes. Não há precedência silenciosa entre os
parâmetros. Ao migrar o frontend, remova `include_tokens` e envie apenas `kind`.
Valores de `kind` são em inglês e minúsculos; valores desconhecidos ou vazios são inválidos.
Omitir `kind` aplica a limpeza física padrão.

Esse filtro não restringe a consulta individual, a consulta em lote nem a resolução
de IDs, que continuam permitindo acesso aos acessórios solicitados.

### Operadores de cores e seleção de incolores

Os operadores comparam as cores oficiais da carta ou de uma mesma face, em qualquer
idioma da busca. Não calculam identidade de cor nem combinam cores de faces distintas.

| Consulta | Correspondência |
| --- | --- |
| `colors=U,R&colors_mode=any` | Azul ou vermelho; outras cores são permitidas. |
| `colors=U,R&colors_mode=all` | Azul e vermelho juntos; outras cores são permitidas. |
| `colors=U,R&colors_mode=exact` | Somente azul e vermelho. |
| `colors=R,U` | Mesmo resultado que o exemplo `exact`, inclusive a ordem da página. |
| `colorless=true` | Array de cores conhecido e vazio no nível correspondente. |
| `colorless=false` | Array de cores conhecido e não vazio no nível correspondente. |

Omitir os dois filtros não restringe as cores. Campos ausentes ou `null` não
correspondem a `colorless=true` nem a `colorless=false`. Uma carta com uma face
colorida e outra incolor pode aparecer em ambas as consultas, dependendo dos outros
filtros: a regra é por carta/face correspondente, não pela união das cores das faces.

O parâmetro `colors_mode` pode ser omitido (o comportamento efetivo é `exact`);
quando enviado, exige `colors` e usa os valores em minúsculas. Os valores de `colors`
aceitam letras minúsculas e espaços nas extremidades, normalizados para maiúsculas.

Por compatibilidade, `colors=` continua selecionando incolores, como `colorless=true`,
e pode ser combinado com `colors_mode=exact`. Uma seleção vazia com `any` ou `all`
retorna `400`; prefira `colorless=true`. Qualquer envio conjunto de `colorless` e
`colors`/`colors_mode` é rejeitado, mesmo se `colorless=false` ou `colors=`.

Textos, custo, poder, resistência e cores devem corresponder juntos na raiz ou em
uma mesma face. Em traduções, é preservado o `face_index` correspondente. Por exemplo,
`oracle_text=Voar&colors=U&colors_mode=any` exige azul na face com “Voar”; não basta que
outra face seja azul. Os filtros continuam sendo aplicados antes da paginação.

```http
GET /cards/search?lang=en&colors=U,R&colors_mode=any
GET /cards/search?lang=en&colors=R,U&colors_mode=all
GET /cards/search?lang=pt&colors=U,R&colors_mode=exact
GET /cards/search?lang=en&colorless=true
```

### Identidade de cor e Commander

Identidade e cores são filtros independentes, combinados com `AND`. A identidade
vem exclusivamente de `color_identity` da carta inteira, nunca de uma face ou de
um texto traduzido. Uma face incolor pode pertencer a uma carta de identidade colorida.

| Operador com `color_identity=U,R` | Seleção |
| --- | --- |
| `any` | A identidade contém azul ou vermelho; outras cores são permitidas. |
| `all` | A identidade contém azul e vermelho; outras cores são permitidas. |
| `exact` ou operador omitido | A identidade contém somente azul e vermelho. |
| `subset` | A identidade não contém cores fora de azul/vermelho: aceita `[]`, `[U]`, `[R]` e `[U,R]`. |

Todas as comparações ignoram a ordem. `identity_colorless=true` exige identidade
conhecida vazia; `identity_colorless=false` exige identidade conhecida não vazia.
Identidade ausente ou `null` é desconhecida e não corresponde a nenhum filtro de
identidade, inclusive `subset`. Omitir os filtros de identidade não exclui esses dados.

Para Commander, envie a união das identidades dos comandantes em `color_identity`
e use `color_identity_mode=subset`. Acrescente `format=commander` para filtrar também
legalidade e `kind=cards` para excluir acessórios. A API não calcula essa união nem
substitui as demais validações de construção do Deck Builder.

```http
GET /cards/search?lang=en&kind=cards&format=commander&color_identity=U,R&color_identity_mode=subset
GET /cards/search?lang=pt&identity_colorless=true
GET /cards/search?lang=en&colorless=true&color_identity=U,R&color_identity_mode=exact
```

`color_identity=` com operador omitido, `exact` ou `subset` seleciona somente
identidades conhecidas vazias; é útil para comandantes incolores. Com `any` ou `all`,
a seleção vazia é inválida. As mesmas regras de letras, espaços, duplicação e
componentes vazios usadas em `colors` se aplicam à identidade. Um operador sem
`color_identity`, valores desconhecidos ou qualquer combinação de `identity_colorless`
com `color_identity`/`color_identity_mode` retorna `400`.

Combinar `colors`/`colorless` com filtros de identidade é permitido. Os filtros
textuais e mecânicos continuam exigindo correspondência na mesma face, enquanto a
identidade restringe a carta inteira, antes da paginação e de `hasNext`.

### Legalidade por formato

Informe um ou mais formatos por consulta, separados por vírgula ou repetindo `format`.
A carta corresponde se possuir um dos status selecionados em pelo menos um formato.
Sem `legality`, `format` seleciona os status
`legal` e `restricted`. Para distinguir os dois, informe o status explicitamente:

```http
GET /cards/search?lang=en&format=commander
GET /cards/search?lang=en&format=modern,pioneer
GET /cards/search?lang=en&format=modern&format=future_format
GET /cards/search?lang=en&format=vintage&legality=restricted
GET /cards/search?lang=en&format=vintage&legality=legal,restricted
GET /cards/search?lang=en&format=modern&legality=banned&colors=R
```

- Status aceitos: `legal`, `restricted`, `not_legal`, `banned`.
- Formatos e status usam `OR`; o grupo de legalidade e os demais filtros usam `AND`.
- Formatos e status aceitam maiúsculas e espaços nas extremidades; valores repetidos
  são deduplicados. Status desconhecidos e componentes vazios retornam `400`.
- `legality` exige `format`. `format` também pode ser usado sozinho.
- A API não mantém uma lista de formatos. Aceita identificadores de até 100 caracteres
  com padrão `[a-z][a-z0-9_]*`, após normalização. Pontos e operadores MongoDB são
  rejeitados. Um identificador novo pode ser consultado sem atualização do backend;
  se estiver ausente no dataset local, simplesmente não corresponde.
- O filtro consulta `legalities.<formato>` da carta inteira, antes da paginação,
  independentemente das faces. Campo ou formato ausente não corresponde a nenhum
  status, nem mesmo `not_legal`.
- As respostas da busca e da consulta individual incluem o mapa completo
  `legalities`, inclusive em traduções. Cartas antigas sem o campo retornam `{}`.
- O idioma padrão continua sendo `pt` e exige tradução cadastrada. Use `lang=en`
  para pesquisar todo o catálogo oficial. Sem `kind`, layouts não pertencentes a decks
  e cartas oversized são excluídos por padrão.
- Os status refletem a última sincronização local; a busca não consulta o Scryfall.
  Sem correspondências, a busca retorna `200` com `items: []` e `hasNext: false`.

### Busca de comandantes

```http
GET /cards/search?lang=en&is_commander=true
GET /cards/search?lang=pt&is_commander=true&color_identity=U,R&color_identity_mode=subset
GET /cards/search?lang=en&is_commander=true&format=modern,pioneer
```

O filtro exige `legalities.commander=legal` e uma criatura lendária (incluindo
criaturas artefato ou encantamento), texto Oracle com permissão explícita
`can be your commander`, ou a exceção de Grist, the Hunger Tide, que é criatura
fora do campo de batalha conforme suas regras oficiais.
Usa os dados oficiais em inglês, mesmo quando a busca e a resposta são traduzidas.
Com `card_faces`, avalia somente a primeira face (`card_faces.0`), sem aceitar
tipos ou permissões do verso nem da linha de tipo combinada. Os demais filtros
continuam podendo corresponder ao verso conforme as regras normais de busca.

`is_commander` não exige `format`. Quando ambos são informados, a elegibilidade
é combinada com o grupo de formatos/status por `AND`; a seleção entre formatos
continua usando `OR`. Um status `banned` em Commander nunca é aceito com
`is_commander=true`, mesmo que outro formato corresponda. `false` seleciona o
complemento completo, incluindo cartas banidas ou sem legalidade informada,
respeitando os demais filtros. A omissão mantém a busca existente.

Este filtro seleciona comandantes independentes; não valida pares de Partner,
Doctor's companion ou Backgrounds que dependem de outro comandante. A legalidade
reflete a última sincronização local, sem chamadas ao Scryfall durante a busca.

### Busca em inglês

Quando `lang=en`, filtros textuais e mecânicos são aplicados em `oracle_cards`,
considerando os campos principais e as faces:

```bash
curl 'http://localhost:8002/cards/search?lang=en&name=Lightning%20Bolt'
```

Para exigir o nome completo com igualdade, use `name_exact`:

```bash
curl 'http://localhost:8002/cards/search?lang=en&name_exact=Lightning%20Bolt'
```

O nome exato pode ser compartilhado por uma carta e um token, como `Ornithopter`.
Sem `kind`, as buscas aplicam a limpeza física padrão descrita acima,
antes da paginação e em qualquer idioma. Use
`/cards/search?lang=en&name_exact=Ornithopter&kind=cards` para buscar a carta;
troque por `kind=all` para incluir os tokens de mesmo nome. Consultas individuais
e em lote por `oracle_id` continuam permitindo acesso aos tokens.

Se `name` e `name_exact` forem informados juntos, ambos devem corresponder (`AND`).

```bash
curl 'http://localhost:8002/cards/search?lang=en&type_line=Creature&cmc_gte=2&cmc_lte=4&limit=20'
```

```bash
curl 'http://localhost:8002/cards/search?lang=en&colors=R&mana_cost=%7BR%7D&cmc=1'
```

### Busca traduzida

Quando `lang` é diferente de `en`, a API exige uma tradução no idioma solicitado:

1. Localiza os `oracle_id` disponíveis em `translations`.
2. Aplica filtros textuais sobre a tradução, quando informados, registrando os
   índices das faces correspondentes.
3. Busca os dados mecânicos correspondentes em `oracle_cards`.
4. Aplica os filtros mecânicos nas mesmas faces identificadas pelo `face_index`.
   Sem filtros textuais por face, consulta os atributos principais ou uma mesma face.
5. Descarta traduções indisponíveis ou estruturalmente inválidas e sobrescreve os
   campos textuais com as traduções válidas.
6. Conta somente resultados válidos para aplicar `offset`, preencher a página e
   determinar `hasNext`.

Não há fallback silencioso para inglês. Se nenhuma tradução válida corresponder,
a busca retorna `200 OK` com `items: []` e `hasNext: false`, mesmo que existam
cartas oficiais com os atributos solicitados.

```bash
curl 'http://localhost:8002/cards/search?lang=pt&name=Raio'
```

```bash
curl 'http://localhost:8002/cards/search?lang=pt&colors=R&cmc=1&limit=5'
```

### Busca por faces

- `name` e `name_exact` consultam o nome principal, que contém os nomes unidos por ` // `.
  `name` faz busca parcial; `name_exact` exige igualdade exata.
  Não restringe qual face deve atender aos demais filtros.
- `cmc`, `cmc_gte` e `cmc_lte` consultam somente o valor principal da carta;
  não calculam valores individuais a partir dos custos das faces.
- `oracle_text`, `type_line`, `mana_cost`, `colors`, `power` e `toughness`
  devem corresponder juntos aos campos principais ou a uma mesma face.
- `oracle_text` e `type_line` usam correspondência parcial literal, sem distinguir maiúsculas.
  Custo, poder e resistência usam igualdade exata; cores usam o operador escolhido,
  com `exact` por padrão e sem depender da ordem.
- Em traduções, o texto e os atributos mecânicos devem corresponder ao mesmo
  `face_index`, independentemente da ordem das faces no documento da tradução.
- Por exemplo, `type_line=Land&power=2` não combina o tipo de uma face com o
  poder de outra. `oracle_text=Voar&power=4` exige poder 4 na face com esse texto.
- Os filtros são aplicados antes de `offset` e `limit`. Cada carta aparece uma
  única vez, mesmo quando várias faces correspondem; a resposta inclui todas as faces.

```bash
curl 'http://localhost:8002/cards/search?lang=en&oracle_text=Flying&power=4'
curl 'http://localhost:8002/cards/search?lang=pt&oracle_text=Voar&power=4'
```

### Resposta da busca

A resposta é um objeto paginado. Exemplo com campos da carta abreviados:

```json
{
  "items": [
    {
      "id": "id-da-impressao-atual",
      "oracle_id": "oracle-id-estavel",
      "lang": "pt",
      "printing_lang": "en",
      "name": "Raio",
      "colors": ["R"],
      "color_identity": ["R"],
      "cmc": 1
    }
  ],
  "limit": 50,
  "offset": 0,
  "hasNext": false
}
```

- `items` contém as cartas válidas da página, no mesmo DTO da consulta individual.
- `limit` e `offset` refletem os parâmetros solicitados, incluindo seus padrões.
- `offset` conta resultados válidos, não documentos candidatos descartados.
- `hasNext` é `true` somente se existe outra carta válida além da página.
- Uma página com exatamente `limit` itens ainda pode ter `hasNext: false`.
- Traduções inválidas entre resultados não encurtam páginas intermediárias nem
  causam término antecipado. Uma tradução que desaparece durante a leitura é
  tratada como indisponível.
- Sem resultados ou com deslocamento além do fim, a resposta é `200`, com
  `items: []` e `hasNext: false`, preservando o `offset` solicitado.
- A ordenação permanece pelo nome oficial e pelo `_id` como desempate.
  Não há total de resultados nesta resposta.

Para avançar, mantenha os filtros e aumente `offset` pelo `limit` solicitado
quando `hasNext` for `true`. Consumidores da resposta antiga em lista devem
passar a ler `response.items` e encerrar a navegação por `response.hasNext`.

Na busca traduzida, os candidatos são examinados em lotes de até 200 documentos
a partir do início da consulta para contar corretamente os resultados válidos.
Isso evita carregar todas as cartas completas em memória, mas deslocamentos altos
podem exigir mais leituras. O mapeamento de traduções candidatas continua sendo
carregado pela busca de traduções.

| Status | Motivo |
| --- | --- |
| `200 OK` | Página retornada, inclusive vazia. |
| `400 Bad Request` | Filtros ou parâmetros de paginação inválidos ou conflitantes. |

### Consultar carta pelo oracle_id

```http
GET /cards/{oracle_id}
```

Retorna um único objeto de carta, com os mesmos campos de cada item da busca. O `oracle_id`
é a identidade estável da carta, não o `id` de uma impressão. Aceita letras,
números e hífens, com até 100 caracteres.

O parâmetro opcional `lang` tem padrão `pt`. Use `lang=en` para consultar
os dados oficiais em inglês. Para outros idiomas, é necessário existir uma
tradução; não há fallback para inglês.

```bash
curl 'http://localhost:8002/cards/oracle-id-estavel?lang=en'
```

| Status | Motivo |
| --- | --- |
| `200 OK` | Carta encontrada no idioma solicitado. |
| `404 Not Found` | Carta ou tradução não encontrada. |
| `422 Unprocessable Entity` | `oracle_id` ou idioma inválido. |

## CRUD de traduções

Traduções em inglês não podem ser cadastradas porque `oracle_cards` já contém o texto
oficial em inglês. Idiomas recebidos pelo CRUD são normalizados, por exemplo `PT`
vira `pt`.

### Criar tradução

```http
POST /translations
Content-Type: application/json
```

```json
{
  "oracle_id": "oracle-id-obtido-na-busca",
  "lang": "pt",
  "name": "Raio",
  "oracle_text": "Raio causa 3 pontos de dano a qualquer alvo.",
  "type_line": "Mágica Instantânea",
  "flavor_text": null
}
```

Campos:

| Campo | Obrigatório | Regra |
| --- | --- | --- |
| `oracle_id` | sim | Deve existir em `oracle_cards`; aceita letras, números e hífens. |
| `lang` | sim | Código como `pt` ou `es`; `en` é rejeitado. |
| `name` | cartas comuns | Entre 1 e 300 caracteres; em multiface, é derivado das faces. |
| `oracle_text` | não | Texto traduzido ou `null`. |
| `type_line` | não | Linha de tipo traduzida ou `null`. |
| `flavor_text` | não | Texto de ambientação traduzido ou `null`. |
| `card_faces` | cartas multiface | Lista completa de traduções por face; proibida em cartas comuns. |

Respostas:

- `201 Created`: tradução criada.
- `404 Not Found`: `oracle_id` não existe em `oracle_cards`.
- `409 Conflict`: já existe tradução para o mesmo `(oracle_id, lang)`.
- `422 Unprocessable Entity`: corpo inválido ou tentativa de tradução `en`.

### Traduções de cartas com múltiplas faces

A API consulta a carta original em `oracle_cards`. Se ela possui duas ou mais
entradas em `card_faces`, os textos devem ser enviados exclusivamente por face:

```json
{
  "oracle_id": "oracle-id-multiface",
  "lang": "pt",
  "card_faces": [
    {"face_index": 0, "name": "Frente", "oracle_text": "Texto da frente."},
    {"face_index": 1, "name": "Verso", "type_line": "Criatura — Lobisomem"}
  ]
}
```

- `face_index` é um inteiro a partir de zero e corresponde à posição da face original.
- Todas as faces devem ser traduzidas, sem índices repetidos, ausentes ou extras.
- Cada face exige `name`; `oracle_text`, `type_line` e `flavor_text` são opcionais,
  com os mesmos limites dos campos de cartas comuns. Campos desconhecidos são rejeitados.
- Custos, cores, poder e resistência continuam vindo exclusivamente da carta original.
- As faces são ordenadas por índice. O nome principal é gerado com ` // `;
  os demais campos textuais principais ficam `null` para cartas multiface.
- Criar uma tradução multiface com textos na raiz, ou enviar faces para uma carta
  comum, retorna `422`. Carta comum exige nome na raiz.
- As respostas de cartas traduzem cada face e preservam seus atributos mecânicos.
  Textos opcionais não traduzidos ficam `null`, sem preenchimento com inglês.
  Uma tradução incompleta por face é considerada indisponível (`404` na consulta individual).

As buscas também consultam `card_faces`, conforme as regras de busca por faces
abaixo.

### Listar traduções

```http
GET /translations
```

Parâmetros opcionais:

| Parâmetro | Padrão | Descrição |
| --- | --- | --- |
| `oracle_id` | — | Filtra pela identidade estável da carta. |
| `lang` | — | Filtra pelo idioma e normaliza o código. |
| `limit` | `50` | De 1 a 200 registros. |
| `offset` | `0` | De 0 a 100.000 registros ignorados. |

```bash
curl 'http://localhost:8002/translations?lang=pt&limit=50&offset=0'
```

Uma consulta sem resultados retorna `200 OK` com `[]`.

### Consultar tradução por ID

```http
GET /translations/{translation_id}
```

O `translation_id` é o `ObjectId` da tradução, não o `oracle_id` da carta.

```bash
curl 'http://localhost:8002/translations/66e57fd92d4ca2713718c123'
```

- `400 Bad Request`: ID não é um `ObjectId` válido.
- `404 Not Found`: tradução não encontrada.

### Consultar por carta e idioma

```http
GET /translations/by-card/{oracle_id}/{lang}
```

```bash
curl 'http://localhost:8002/translations/by-card/oracle-id-estavel/pt'
```

Retorna `404 Not Found` quando o par não possui tradução.

### Atualizar tradução

```http
PATCH /translations/{translation_id}
Content-Type: application/json
```

```json
{
  "name": "Raio Revisado",
  "oracle_text": "Raio causa 3 pontos de dano a qualquer alvo.",
  "flavor_text": null
}
```

- Pelo menos um campo deve ser informado.
- Podem ser alterados `name`, `oracle_text`, `type_line` e `flavor_text`.
- Para cartas multiface, envie somente `card_faces`, com a lista completa: ela
  substitui a lista anterior, incluindo os textos opcionais. Não é uma mesclagem
  parcial das faces. `card_faces: null` é rejeitado.
- A estrutura final é validada contra a carta original antes de salvar, também
  no `PATCH`. Carta original ausente retorna `404`.
- `oracle_id` e `lang` são imutáveis.
- `name` não aceita `null`.
- Os outros campos podem receber `null` para remover o conteúdo traduzido.
- Um ID inválido retorna `400`; uma tradução ausente retorna `404`; corpo inválido
  retorna `422`.

### Excluir tradução

```http
DELETE /translations/{translation_id}
```

- `204 No Content`: tradução excluída.
- `400 Bad Request`: ID inválido.
- `404 Not Found`: tradução não encontrada.

## Desenvolvimento local

```bash
python -m pip install -e '.[dev]'
pytest
```

Os testes de integração de buscas em faces exigem um MongoDB de teste. Eles
criam bancos temporários com nomes únicos e os removem ao terminar. Sem a variável
abaixo, esses testes são pulados:

```bash
TEST_MONGODB_URI=mongodb://localhost:27028 pytest tests/test_face_search.py -q
```

Para executar a API sem Docker, disponibilize um MongoDB local e use:

```bash
MONGODB_URI=mongodb://localhost:27017 uvicorn app.main:app --reload
```

## Créditos e aviso legal

Os dados de cartas e as URLs de imagens utilizados por este projeto são obtidos por
meio do [Scryfall](https://scryfall.com/) e de seu
[Bulk Data](https://scryfall.com/docs/api/bulk-data). Agradecemos ao Scryfall por
manter essa infraestrutura disponível à comunidade. Este projeto não é afiliado,
patrocinado ou endossado pelo Scryfall.

Magic: The Gathering, nomes de cartas, textos, símbolos, imagens e demais materiais
relacionados são propriedade de seus respectivos titulares, incluindo a Wizards of
the Coast. O **MTG Card Manager é conteúdo de fã não oficial; não é aprovado nem
endossado pela Wizards of the Coast**. Partes dos materiais utilizados pertencem à
Wizards of the Coast. © Wizards of the Coast LLC.

A finalidade deste software é catalogação, pesquisa técnica e gerenciamento de
traduções. Ele não foi criado para fabricar cartas falsificadas ou proxies, redistribuir
obras protegidas sem autorização, substituir produtos oficiais ou facilitar pirataria.
Quem operar ou distribuir o serviço é responsável por respeitar licenças, direitos
autorais, marcas e os termos aplicáveis.

Consulte a
[Política de Conteúdo de Fãs da Wizards of the Coast](https://company.wizards.com/pt-BR/legal/fancontentpolicy)
antes de publicar ou explorar uma instalação deste projeto.
