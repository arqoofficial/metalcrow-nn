from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.schemas.search import CorpusSearchRequest, CorpusSearchResponse
from app.services import search as search_service

router = APIRouter(
    prefix="/search", tags=["search"], dependencies=[Depends(get_current_user)]
)


@router.post("", response_model=CorpusSearchResponse)
def corpus_search(body: CorpusSearchRequest) -> CorpusSearchResponse:
    """POST /api/v1/search — поиск по корпусу.

    Пассажи онтологии (выводы/измерения с дословным сниппетом, источником и
    распознанными сущностями запроса) + обработанные markdown-документы.
    """
    return search_service.corpus_search(body)
