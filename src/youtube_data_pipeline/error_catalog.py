from __future__ import annotations

from typing import Any

ERROR_CATALOG: dict[str, dict[str, dict[str, Any]]] = {
    "commentThreads.list": {
        "operationNotSupported": {
            "status": 400,
            "reason": "operationNotSupported",
            "description": (
                "A solicitação à API é inválida ou foi formada incorretamente. "
                "Consequentemente, o servidor da API não identificou a solicitação."
            ),
        },
        "processingFailure": {
            "status": 400,
            "reason": "processingFailure",
            "description": (
                "O servidor da API não conseguiu processar a solicitação. "
                "Embora possa ser um erro temporário, geralmente indica que a entrada da "
                "solicitação é inválida. Verifique a estrutura do recurso commentThread "
                "no corpo da solicitação para garantir que ela seja válida."
            ),
        },
        "commentsDisabled": {
            "status": 403,
            "reason": "commentsDisabled",
            "description": "O vídeo identificado pelo parâmetro videoId tem os comentários desativados.",
        },
        "forbidden": {
            "status": 403,
            "reason": "forbidden",
            "description": (
                "Não é possível recuperar uma ou mais conversas de comentários solicitadas "
                "devido a permissões insuficientes. A solicitação pode não estar devidamente "
                "autorizada."
            ),
        },
        "channelNotFound": {
            "status": 404,
            "reason": "channelNotFound",
            "description": (
                "Não foi possível encontrar o canal identificado pelo parâmetro "
                "allThreadsRelatedToChannelId."
            ),
        },
        "commentThreadNotFound": {
            "status": 404,
            "reason": "commentThreadNotFound",
            "description": (
                "Não foi possível encontrar uma ou mais das sequências de comentários especificadas. "
                "Verifique os valores do parâmetro id da solicitação para garantir que estejam corretos."
            ),
        },
        "videoNotFound": {
            "status": 404,
            "reason": "videoNotFound",
            "description": "Não foi possível encontrar o vídeo identificado pelo parâmetro videoId.",
        },
    },
    "captions.list": {
        "forbidden": {
            "status": 403,
            "reason": "forbidden",
            "description": (
                "Não foi possível recuperar uma ou mais faixas de legenda porque as permissões "
                "associadas à solicitação não são suficientes para recuperar os recursos solicitados. "
                "A solicitação pode não estar devidamente autorizada."
            ),
        },
        "captionNotFound": {
            "status": 404,
            "reason": "captionNotFound",
            "description": (
                "Não foi possível encontrar uma ou mais faixas de legenda especificadas. "
                "Esse erro ocorre se o parâmetro videoId identificar um vídeo real, mas o "
                "parâmetro id identificar IDs de faixa de legenda que não existem ou IDs de faixa "
                "associados a outros vídeos. Verifique se os valores dos parâmetros id e videoId "
                "da solicitação estão corretos."
            ),
        },
        "videoNotFound": {
            "status": 404,
            "reason": "videoNotFound",
            "description": "Não foi possível encontrar o vídeo identificado pelo parâmetro videoId.",
        },
    },
    "activities.list": {
        "homeParameterDeprecated": {
            "status": 403,
            "reason": "homeParameterDeprecated",
            "description": (
                "Os dados de atividade da página inicial do usuário não estão disponíveis nessa API. "
                "Esse erro pode ocorrer se você definir o parâmetro home como true em uma solicitação "
                "não autorizada."
            ),
        },
        "forbidden": {
            "status": 403,
            "reason": "forbidden",
            "description": "A solicitação não está autorizada corretamente.",
        },
        "channelNotFound": {
            "status": 404,
            "reason": "channelNotFound",
            "description": (
                "Não foi possível encontrar o ID do canal identificado pelo parâmetro channelId "
                "da solicitação."
            ),
        },
        "homeChannelNotFound": {
            "status": 404,
            "reason": "homeChannelNotFound",
            "description": (
                "Não foi possível encontrar um feed da página inicial do YouTube para o usuário "
                "autenticado."
            ),
        },
        "authorizationRequired": {
            "status": 401,
            "reason": "authorizationRequired",
            "description": "A solicitação usa o parâmetro home, mas não está autorizada corretamente.",
        },
    },
    "channels.list": {
        "invalidCriteria": {
            "status": 400,
            "reason": "invalidCriteria",
            "description": (
                "É possível especificar no máximo um dos seguintes filtros: id, mySubscribers, "
                "categoryId, mine, managedByMe, forUsername. No caso da autenticação do proprietário "
                "do conteúdo usando o parâmetro onBehalfOfContentOwner, apenas id ou managedByMe podem "
                "ser especificados."
            ),
        },
        "channelForbidden": {
            "status": 403,
            "reason": "channelForbidden",
            "description": (
                "O canal especificado pelo parâmetro id não é compatível com a solicitação ou ela "
                "não está autorizada corretamente."
            ),
        },
        "categoryNotFound": {
            "status": 404,
            "reason": "categoryNotFound",
            "description": "Não foi possível encontrar a categoria identificada pelo parâmetro categoryId.",
        },
        "channelNotFound": {
            "status": 404,
            "reason": "channelNotFound",
            "description": "Não foi possível encontrar o canal identificado pelo parâmetro id.",
        },
    },
    "comments.list": {
        "operationNotSupported": {
            "status": 400,
            "reason": "operationNotSupported",
            "description": (
                "A solicitação à API é inválida ou foi formada incorretamente. "
                "Consequentemente, o servidor da API não identificou a solicitação."
            ),
        },
        "forbidden": {
            "status": 403,
            "reason": "forbidden",
            "description": (
                "Não é possível recuperar um ou mais dos comentários solicitados devido a permissões "
                "insuficientes. A solicitação pode não estar devidamente autorizada."
            ),
        },
        "commentNotFound": {
            "status": 404,
            "reason": "commentNotFound",
            "description": (
                "Não foi possível encontrar um ou mais dos comentários especificados. "
                "Verifique se os valores dos parâmetros id e parentId da solicitação estão corretos."
            ),
        },
    },
    "search.list": {
        "invalidChannelId": {
            "status": 400,
            "reason": "invalidChannelId",
            "description": "O parâmetro channelId especificou um ID do canal inválido.",
        },
        "invalidLocation": {
            "status": 400,
            "reason": "invalidLocation",
            "description": (
                "O valor do parâmetro location e/ou locationRadius foi formatado incorretamente."
            ),
        },
        "invalidRelevanceLanguage": {
            "status": 400,
            "reason": "invalidRelevanceLanguage",
            "description": (
                "O valor do parâmetro relevanceLanguage foi formatado incorretamente."
            ),
        },
        "invalidSearchFilter": {
            "status": 400,
            "reason": "invalidSearchFilter",
            "description": (
                "A solicitação contém uma combinação inválida de filtros de pesquisa e/ou restrições. "
                "Defina o parâmetro type como video se você definir um valor para os parâmetros "
                "eventType, videoCaption, videoCategoryId, videoDefinition, videoDimension, "
                "videoDuration, videoEmbeddable, videoLicense, videoSyndicated ou videoType."
            ),
        },
    },
    "videos.list": {
        "videoChartNotFound": {
            "status": 400,
            "reason": "videoChartNotFound",
            "description": "O gráfico de vídeo solicitado não é suportado ou não está disponível.",
        },
        "forbidden": {
            "status": 403,
            "reason": "forbidden",
            "description": (
                "A solicitação não está autorizada corretamente para acessar o arquivo de vídeo ou "
                "as informações de processamento. As partes fileDetails, processingDetails e "
                "suggestions estão disponíveis apenas para o proprietário do vídeo. Também pode "
                "ocorrer ao usar o parâmetro myRating sem autorização."
            ),
        },
        "videoNotFound": {
            "status": 404,
            "reason": "videoNotFound",
            "description": (
                "Não foi possível encontrar o vídeo que você está tentando recuperar. "
                "Verifique se o valor do parâmetro id da solicitação está correto."
            ),
        },
    },
    "playlistItems.list": {
        "playlistItemsNotAccessible": {
            "status": 403,
            "reason": "playlistItemsNotAccessible",
            "description": (
                "A solicitação não está autorizada corretamente para recuperar a playlist "
                "especificada."
            ),
        },
        "playlistNotFound": {
            "status": 404,
            "reason": "playlistNotFound",
            "description": (
                "Não foi possível encontrar o canal ou a playlist identificada pelo parâmetro "
                "playlistId da solicitação."
            ),
        },
    },
}


def lookup_error_meta(endpoint: str, reason: str) -> dict[str, Any] | None:
    endpoint_map = ERROR_CATALOG.get(endpoint)
    if not endpoint_map:
        return None
    return endpoint_map.get(reason)


def lookup_error_description(endpoint: str, reason: str) -> str | None:
    meta = lookup_error_meta(endpoint, reason)
    if not meta:
        return None
    return meta.get("description")
