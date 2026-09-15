# MTG Card Manager

Microsserviço em Python e FastAPI para ingerir, armazenar e pesquisar dados de cartas
de Magic: The Gathering. O serviço mantém uma cópia local do Scryfall Oracle Cards e
permite cadastrar traduções próprias, principalmente em `pt-BR`, sem consultar APIs
públicas em tempo real e sem sobrescrever traduções durante o sincronismo diário.

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
  "lang": "pt-BR",
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

A API fica disponível em `http://localhost:8000`.

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI: `http://localhost:8000/openapi.json`
- Health check: `GET http://localhost:8000/health`

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

É obrigatório informar pelo menos um filtro de busca. Os filtros informados são
combinados com lógica `AND`.

### Parâmetros

| Parâmetro | Tipo | Padrão | Comportamento |
| --- | --- | --- | --- |
| `lang` | string | `pt-BR` | Idioma da resposta. Use `en` para dados oficiais ou um idioma com traduções cadastradas. |
| `name` | string | — | Busca parcial por nome, sem diferenciar maiúsculas e minúsculas. |
| `oracle_text` | string | — | Busca parcial no texto Oracle. |
| `type_line` | string | — | Busca parcial na linha de tipo. |
| `colors` | string | — | Cores separadas por vírgula. Aceita somente `W,U,B,R,G`, sem repetição. |
| `mana_cost` | string | — | Correspondência exata do custo, por exemplo `{R}` ou `{1}{U}`. |
| `cmc` | número | — | Valor de mana exato, maior ou igual a zero. |
| `cmc_gte` | número | — | Valor de mana mínimo, inclusivo. |
| `cmc_lte` | número | — | Valor de mana máximo, inclusivo. |
| `power` | string | — | Poder exato, incluindo valores não numéricos como `*`. |
| `toughness` | string | — | Resistência exata. |
| `limit` | inteiro | `50` | Quantidade de resultados, entre 1 e 200. |
| `offset` | inteiro | `0` | Quantidade ignorada para paginação, entre 0 e 100.000. |

Regras importantes:

- `cmc` não pode ser combinado com `cmc_gte` ou `cmc_lte`.
- `cmc_gte` não pode ser maior que `cmc_lte`.
- `colors` usa igualdade exata do array, inclusive a ordem. `colors=U,R` não significa
  “contém azul ou vermelho”.
- Para custos com chaves, faça URL encoding quando necessário: `{R}` vira `%7BR%7D`.
- Recomenda-se usar o formato canônico dos idiomas, como `en` e `pt-BR`.

### Busca em inglês

Quando `lang=en`, filtros textuais e mecânicos são aplicados em `oracle_cards`,
considerando os campos principais e as faces:

```bash
curl 'http://localhost:8000/cards/search?lang=en&name=Lightning%20Bolt'
```

```bash
curl 'http://localhost:8000/cards/search?lang=en&type_line=Creature&cmc_gte=2&cmc_lte=4&limit=20'
```

```bash
curl 'http://localhost:8000/cards/search?lang=en&colors=R&mana_cost=%7BR%7D&cmc=1'
```

### Busca traduzida

Quando `lang` é diferente de `en`, a API exige uma tradução no idioma solicitado:

1. Localiza os `oracle_id` disponíveis em `translations`.
2. Aplica filtros textuais sobre a tradução, quando informados, registrando os
   índices das faces correspondentes.
3. Busca os dados mecânicos correspondentes em `oracle_cards`.
4. Aplica os filtros mecânicos nas mesmas faces identificadas pelo `face_index`.
   Sem filtros textuais por face, consulta os atributos principais ou uma mesma face.
5. Sobrescreve os campos textuais com a tradução.

Não há fallback silencioso para inglês. Se nenhuma tradução corresponder, a API retorna
`404 Not Found`, mesmo que existam cartas oficiais com os atributos solicitados.

```bash
curl 'http://localhost:8000/cards/search?lang=pt-BR&name=Raio'
```

```bash
curl 'http://localhost:8000/cards/search?lang=pt-BR&colors=R&cmc=1&limit=5'
```

### Busca por faces

- `name` consulta o nome principal, que contém os nomes unidos por ` // `.
  Não restringe qual face deve atender aos demais filtros.
- `cmc`, `cmc_gte` e `cmc_lte` consultam somente o valor principal da carta;
  não calculam valores individuais a partir dos custos das faces.
- `oracle_text`, `type_line`, `mana_cost`, `colors`, `power` e `toughness`
  devem corresponder juntos aos campos principais ou a uma mesma face.
- Texto continua usando correspondência parcial literal, sem distinguir maiúsculas.
  Custo, poder e resistência usam igualdade exata; cores exigem o mesmo array e ordem.
- Em traduções, o texto e os atributos mecânicos devem corresponder ao mesmo
  `face_index`, independentemente da ordem das faces no documento da tradução.
- Por exemplo, `type_line=Land&power=2` não combina o tipo de uma face com o
  poder de outra. `oracle_text=Voar&power=4` exige poder 4 na face com esse texto.
- Os filtros são aplicados antes de `offset` e `limit`. Cada carta aparece uma
  única vez, mesmo quando várias faces correspondem; a resposta inclui todas as faces.

```bash
curl 'http://localhost:8000/cards/search?lang=en&oracle_text=Flying&power=4'
curl 'http://localhost:8000/cards/search?lang=pt-BR&oracle_text=Voar&power=4'
```

### Resposta da busca

```json
[
  {
    "id": "id-da-impressao-atual",
    "oracle_id": "oracle-id-estavel",
    "lang": "pt-BR",
    "name": "Raio",
    "oracle_text": "Raio causa 3 pontos de dano a qualquer alvo.",
    "type_line": "Mágica Instantânea",
    "flavor_text": null,
    "colors": ["R"],
    "mana_cost": "{R}",
    "cmc": 1,
    "power": null,
    "toughness": null
  }
]
```

Possíveis respostas:

| Status | Motivo |
| --- | --- |
| `200 OK` | Uma ou mais cartas encontradas. |
| `400 Bad Request` | Filtros ausentes, inválidos ou conflitantes. |
| `404 Not Found` | Nenhuma carta ou tradução correspondente. |

### Consultar carta pelo oracle_id

```http
GET /cards/{oracle_id}
```

Retorna um único objeto de carta, com os mesmos campos da busca. O `oracle_id`
é a identidade estável da carta, não o `id` de uma impressão. Aceita letras,
números e hífens, com até 100 caracteres.

O parâmetro opcional `lang` tem padrão `pt-BR`. Use `lang=en` para consultar
os dados oficiais em inglês. Para outros idiomas, é necessário existir uma
tradução; não há fallback para inglês.

```bash
curl 'http://localhost:8000/cards/oracle-id-estavel?lang=en'
```

| Status | Motivo |
| --- | --- |
| `200 OK` | Carta encontrada no idioma solicitado. |
| `404 Not Found` | Carta ou tradução não encontrada. |
| `422 Unprocessable Entity` | `oracle_id` ou idioma inválido. |

## CRUD de traduções

Traduções em inglês não podem ser cadastradas porque `oracle_cards` já contém o texto
oficial em inglês. Idiomas recebidos pelo CRUD são normalizados, por exemplo `pt-br`
vira `pt-BR`.

### Criar tradução

```http
POST /translations
Content-Type: application/json
```

```json
{
  "oracle_id": "oracle-id-obtido-na-busca",
  "lang": "pt-BR",
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
| `lang` | sim | Código como `pt` ou `pt-BR`; `en` é rejeitado. |
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
  "lang": "pt-BR",
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
curl 'http://localhost:8000/translations?lang=pt-BR&limit=50&offset=0'
```

Uma consulta sem resultados retorna `200 OK` com `[]`.

### Consultar tradução por ID

```http
GET /translations/{translation_id}
```

O `translation_id` é o `ObjectId` da tradução, não o `oracle_id` da carta.

```bash
curl 'http://localhost:8000/translations/66e57fd92d4ca2713718c123'
```

- `400 Bad Request`: ID não é um `ObjectId` válido.
- `404 Not Found`: tradução não encontrada.

### Consultar por carta e idioma

```http
GET /translations/by-card/{oracle_id}/{lang}
```

```bash
curl 'http://localhost:8000/translations/by-card/oracle-id-estavel/pt-BR'
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
