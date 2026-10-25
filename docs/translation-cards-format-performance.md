# Filtros de formato/legalidade na consulta de traduções

## Contrato e escopo

`GET /translations/cards` aceita `format` e `legality` opcionais, separados por vírgula
ou repetidos, normalizados para minúsculas, sem espaços externos e sem duplicatas.
Formatos seguem `[a-z][a-z0-9_]*`, de 1 a 100 caracteres, sem catálogo fixo no backend.
Status: `legal`, `restricted`, `banned`, `not_legal`. Componentes vazios são inválidos.

Uma carta corresponde quando um dos formatos tem um dos status selecionados.
`format` sozinho seleciona `legal,restricted`; `legality` sozinho retorna 422.
Campos ausentes não representam `not_legal`. Em `kind=accessories`, ambos são
proibidos (422). Erros têm `detail[].loc` começando com `query` e o parâmetro;
erros de itens incluem também a posição. Parâmetros desconhecidos continuam proibidos.

```http
GET /translations/cards?lang=pt&kind=cards&translation_status=missing&format=standard,modern,pioneer,legacy,commander&legality=legal,restricted&limit=24&offset=0
GET /translations/cards?lang=pt&kind=cards&format=vintage
GET /translations/cards?lang=pt&kind=cards&format=modern&legality=banned
```

Sem os parâmetros novos, a abrangência anterior permanece, inclusive cartas sem
legalidades, oversized, planar e scheme conforme a categoria existente. O contrato
público de `/cards/search` permanece igual: apenas seus normalizadores, tipo de
identificador e predicado MongoDB foram extraídos para funções compartilhadas.
As exigências específicas de `kind=cards` da busca pública não são importadas.

Nome, categoria e legalidades entram no primeiro `$match`. Ordenação binária simples
por `name, oracle_id` vem antes do join, usando o índice existente. Para `missing` e
`present`, existência é filtrada antes de `$skip`/`$limit`; em `all`, somente a página
mais sentinela precisa do join. Uma agregação, sem consultas individuais ou cache.
O serviço e o DTO permanecem iguais: `items/limit/offset/hasNext`, inglês completo,
`translation_id` obrigatório/anulável, uma entrada por oracle_id, faces preservadas.
Existência continua contando qualquer registro no idioma, sem validar seu conteúdo.

## Validação com MongoDB real

Suíte completa: **508 passed**, sem skips, MongoDB **7.0.45**, Python 3.11.
Avisos de depreciação de Starlette/httpx e do nome da constante HTTP 422 não impedem
os testes. `ruff check app tests scripts` e `git diff --check` passam.

Comando de teste, em ambiente com as dependências de desenvolvimento instaladas:

```bash
TEST_MONGODB_URI=mongodb://mtg-card-manager-db:27017 python -m pytest -p no:cacheprovider -q
```

A execução desta entrega usou um container descartável na rede do MongoDB existente,
com cópia do código e dependências; nenhum serviço em execução foi atualizado.
Cada fixture cria e remove um banco exclusivo. Os testes incluem coleção com
collation não binária para comprovar a comparação `simple` explícita.

Evidências funcionais em `tests/test_translation_cards.py`:

- Legal em apenas um formato; restrita em um e banida em outro; sem correspondência,
  mapa vazio e campo ausente; `not_legal` exige valor explícito; formato dinâmico.
- Nome literal, categoria, idioma, existência e legalidade combinados; tradução
  inutilizável ainda presente; DTO multiface completo e uma entrada por identidade.
- Páginas com limites 1, 2 e 3, offsets consecutivos e após o fim, nomes empatados,
  página cheia sem sucessora, vazia e `hasNext` calculado pela sentinela.
- Regressões sem filtros novos, incluindo oráculo Python em conjuntos de 500 cartas
  com diferentes densidades de tradução, pontuação literal e comparação binária.
- Normalização, defaults, limites e 422 localizados; rejeição em acessórios e extras;
  OpenAPI contém os dois parâmetros e mantém schemas de resposta.
- Criação/exclusão imediatamente refletidas com filtros; atualização preserva ID e
  existência nos testes existentes. Busca pública mantém ordenação e legalidade.
- Seis planos reais: `all/missing/present` com/sem formato, índice de ordenação sem
  estágio SORT bloqueante e join sem collection scans. Dados com 2.000 cartas
  distinguem seleção antes da paginação e trabalho de join limitado ao necessário.

## Medição no catálogo

Snapshot descartável de **38.991 cartas e 21.888 traduções**, sem escrita no banco
fonte. O script adiciona 7 traduções de outro idioma somente no snapshot.
Cinco repetições por variante, uma execução de aquecimento descartada; cursor
inteiramente consumido e documentos completos comparados, incluindo a sentinela.
Medições locais de agregação, sem HTTP; não são previsão de latência de produção.
As duas séries iniciais tiveram sobreposição de execução e compartilham recursos:
pequenas diferenças entre séries não devem ser interpretadas como regressão precisa.

Medianas em milissegundos, página inicial `limit=24`, idioma pt:

| Filtros | Status | Join antes de ordenar (referência histórica) | Consulta otimizada preservada |
|---|---|---:|---:|
| Sem formato | missing | 3783,133 | 6,060 |
| Sem formato | present | 3721,201 | 12,766 |
| Sem formato | all | 3,477 | 3,543 |
| Cinco formatos, legal/restricted | missing | 3176,073 | 8,639 |
| Cinco formatos, legal/restricted | present | 3187,176 | 10,548 |
| Cinco formatos, legal/restricted | all | 3,642 | 3,904 |

A referência histórica recebe os mesmos filtros; ela isola o benefício da otimização
já existente, não representa o código imediatamente anterior a esta extensão.
Todos os pares retornaram documentos BSON integralmente idênticos. Sem os filtros,
a estrutura da agregação permanece igual à implementação anterior desta extensão.

Nos planos iniciais, a ordenação usa `ix_oracle_cards_name_oracle_id`, sem SORT
bloqueante. O lookup usa `uq_translation_oracle_lang`, com zero collection scans.
Candidatos processados pelo lookup para `missing/present/all`: **28/104/25** sem
filtros, **31/69/25** com filtros. A seleção de legalidade ocorre antes desse join.
Em `all`, o join continua limitado aos 25 itens, independentemente da abrangência.

Offset 10.000 com os cinco formatos, três repetições (ms):

| Status | Referência histórica | Consulta otimizada preservada |
|---|---:|---:|
| missing | 3128,493 | 3097,930 |
| present | 3166,335 | 1438,853 |
| all | 39,386 | 41,782 |

Também houve igualdade integral dos documentos nos três casos. O custo de `missing`
continua alto em páginas profundas; os filtros não eliminam a necessidade de verificar
existência dos candidatos anteriores ao offset. Não há promessa de melhoria uniforme.

Planos completos e amostras de tempo:

- [Sem filtros](evidence/translation-cards-initial.json)
- [Com filtros](evidence/translation-cards-filtered-initial.json)
- [Com filtros e offset 10.000](evidence/translation-cards-filtered-offset.json)

Reprodução a partir da raiz (URI com acesso a MongoDB e um catálogo fonte):

```bash
PYTHONPATH=. python scripts/benchmark_translation_cards.py --uri mongodb://mtg-card-manager-db:27017 --only initial --repeats 5 --output /tmp/initial.json
PYTHONPATH=. python scripts/benchmark_translation_cards.py --uri mongodb://mtg-card-manager-db:27017 --only filtered_initial --repeats 5 --output /tmp/filtered.json
PYTHONPATH=. python scripts/benchmark_translation_cards.py --uri mongodb://mtg-card-manager-db:27017 --only filtered_offset_10000 --repeats 3 --output /tmp/deep.json
```

O script também oferece `filtered_after_end` e `filtered_name`; omitir `--only`
executa a matriz completa e as ablações existentes. Os casos disponíveis não devem
ser confundidos com os casos efetivamente medidos nesta entrega.

## Índices e operação

Nenhum índice novo ou migração. Preservados os índices existentes, criados no startup:

- `oracle_cards`: `oracle_id` único e `ix_oracle_cards_name_oracle_id` em
  `(name:1, oracle_id:1)` com collation `simple`.
- `translations`: `(oracle_id:1, lang:1)` único.

Aplicar o código pelo processo normal de build/restart do Card Manager; o startup
assegura os índices. Não foi feito deploy. Filtros seletivos podem exigir examinar
mais chaves do índice ordenado até completar uma página; offsets profundos e
`missing/present` com poucos resultados continuam podendo percorrer o conjunto todo.
Não se adicionam índices por formato nem cache. A existência reflete o CRUD seguinte.

## Arquivos

- `app/legalities.py`: identificador, normalizadores e predicado compartilhados.
- `app/schemas.py`, `app/repository.py`: delegação pública aos mesmos helpers,
  preservando validação e consulta anteriores.
- `app/translation_schemas.py`: parâmetros, defaults e validação localizada.
- `app/translation_repository.py`: legalidades no primeiro match.
- `app/translation_api.py`: descrição OpenAPI, gerada em `/openapi.json`.
- `tests/test_translation_cards.py`: contrato HTTP, integração e planos ampliados.
- `scripts/benchmark_translation_cards.py`: cenários com filtros, mantendo ablações.
- `README.md`, este documento e JSONs de evidência: contrato e validação.

Nenhum arquivo de mtg-tools foi alterado.
