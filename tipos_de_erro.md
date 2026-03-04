# Tipos de erro — YouTube Data API v3

Este catálogo consolida os erros por endpoint usados pela pipeline.

## commentThreads.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| badRequest (400) | operationNotSupported | A solicitação à API é inválida ou foi formada incorretamente. Consequentemente, o servidor da API não identificou a solicitação. |
| badRequest (400) | processingFailure | O servidor da API não conseguiu processar a solicitação. Embora possa ser um erro temporário, geralmente indica que a entrada da solicitação é inválida. Verifique a estrutura do recurso commentThread no corpo da solicitação para garantir que ela seja válida. |
| forbidden (403) | commentsDisabled | O vídeo identificado pelo parâmetro videoId tem os comentários desativados. |
| forbidden (403) | forbidden | Não é possível recuperar uma ou mais conversas de comentários solicitadas devido a permissões insuficientes. A solicitação pode não estar devidamente autorizada. |
| notFound (404) | channelNotFound | Não foi possível encontrar o canal identificado pelo parâmetro allThreadsRelatedToChannelId. |
| notFound (404) | commentThreadNotFound | Não foi possível encontrar uma ou mais das sequências de comentários especificadas. Verifique os valores do parâmetro id da solicitação para garantir que estejam corretos. |
| notFound (404) | videoNotFound | Não foi possível encontrar o vídeo identificado pelo parâmetro videoId. |

## captions.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| forbidden (403) | forbidden | Não foi possível recuperar uma ou mais faixas de legenda porque as permissões associadas à solicitação não são suficientes para recuperar os recursos solicitados. A solicitação pode não estar devidamente autorizada. |
| notFound (404) | captionNotFound | Não foi possível encontrar uma ou mais faixas de legenda especificadas. Esse erro ocorre se o parâmetro videoId identificar um vídeo real, mas o parâmetro id identificar IDs de faixa de legenda que não existem ou IDs de faixa associados a outros vídeos. Verifique se os valores dos parâmetros id e videoId da solicitação estão corretos. |
| notFound (404) | videoNotFound | Não foi possível encontrar o vídeo identificado pelo parâmetro videoId. |

## activities.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| forbidden (403) | homeParameterDeprecated | Os dados de atividade da página inicial do usuário não estão disponíveis nessa API. Esse erro pode ocorrer se você definir o parâmetro home como true em uma solicitação não autorizada. |
| forbidden (403) | forbidden | A solicitação não está autorizada corretamente. |
| notFound (404) | channelNotFound | Não foi possível encontrar o ID do canal identificado pelo parâmetro channelId da solicitação. |
| notFound (404) | homeChannelNotFound | Não foi possível encontrar um feed da página inicial do YouTube para o usuário autenticado. |
| unauthorized (401) | authorizationRequired | A solicitação usa o parâmetro home, mas não está autorizada corretamente. |

## channels.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| badRequest (400) | invalidCriteria | É possível especificar no máximo um dos seguintes filtros: id, mySubscribers, categoryId, mine, managedByMe, forUsername. No caso da autenticação do proprietário do conteúdo usando o parâmetro onBehalfOfContentOwner, apenas id ou managedByMe podem ser especificados. |
| forbidden (403) | channelForbidden | O canal especificado pelo parâmetro id não é compatível com a solicitação ou ela não está autorizada corretamente. |
| notFound (404) | categoryNotFound | Não foi possível encontrar a categoria identificada pelo parâmetro categoryId. |
| notFound (404) | channelNotFound | Não foi possível encontrar o canal identificado pelo parâmetro id. |

## comments.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| badRequest (400) | operationNotSupported | A solicitação à API é inválida ou foi formada incorretamente. Consequentemente, o servidor da API não identificou a solicitação. |
| forbidden (403) | forbidden | Não é possível recuperar um ou mais dos comentários solicitados devido a permissões insuficientes. A solicitação pode não estar devidamente autorizada. |
| notFound (404) | commentNotFound | Não foi possível encontrar um ou mais dos comentários especificados. Verifique se os valores dos parâmetros id e parentId da solicitação estão corretos. |

## search.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| badRequest (400) | invalidChannelId | O parâmetro channelId especificou um ID do canal inválido. |
| badRequest (400) | invalidLocation | O valor do parâmetro location e/ou locationRadius foi formatado incorretamente. |
| badRequest (400) | invalidRelevanceLanguage | O valor do parâmetro relevanceLanguage foi formatado incorretamente. |
| badRequest (400) | invalidSearchFilter | A solicitação contém uma combinação inválida de filtros de pesquisa e/ou restrições. Defina o parâmetro type como video se você definir um valor para os parâmetros eventType, videoCaption, videoCategoryId, videoDefinition, videoDimension, videoDuration, videoEmbeddable, videoLicense, videoSyndicated ou videoType. |

## videos.list

| Tipo de erro | Detalhe do erro | Descrição |
| --- | --- | --- |
| badRequest (400) | videoChartNotFound | O gráfico de vídeo solicitado não é suportado ou não está disponível. |
| forbidden (403) | forbidden | A solicitação não está autorizada corretamente para acessar o arquivo de vídeo ou as informações de processamento. As partes fileDetails, processingDetails e suggestions estão disponíveis apenas para o proprietário do vídeo. Também pode ocorrer ao usar o parâmetro myRating sem autorização. |
| notFound (404) | videoNotFound | Não foi possível encontrar o vídeo que você está tentando recuperar. Verifique se o valor do parâmetro id da solicitação está correto. |
