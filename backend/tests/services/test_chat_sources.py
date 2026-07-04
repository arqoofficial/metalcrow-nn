"""GraphRAG source provenance → wiki deep-links (SPEC_PATCH_clickable_sources_wiki)."""

from app.services import chat as chat_service


def test_shared_raw_path_candidates_inserts_known_subfolders() -> None:
    flat = "RAW_DATA/Доклад_Вострикова Н.М.pdf"
    candidates = chat_service._shared_raw_path_candidates(flat)
    assert candidates[0] == "RAW_DATA/Доклады/Доклад_Вострикова Н.М.pdf"
    assert flat in candidates


def test_resolve_chat_sources_resolves_flattened_tree_paths(
    monkeypatch,
) -> None:
    flat = "RAW_DATA/Доклад_Вострикова Н.М.pdf"
    okf = "01_docling_clean00/RAW_DATA/Доклады/Доклад_Вострикова Н.М.pdf.md"

    def fake_get_document_content(path: str):
        return object() if path == okf else None

    monkeypatch.setattr(
        chat_service.wiki, "get_document_content", fake_get_document_content
    )
    monkeypatch.setattr(
        chat_service.wiki, "okf_to_raw_path", lambda p: "RAW_DATA/Доклады/Доклад_Вострикова Н.М.pdf"
    )

    sources = chat_service._resolve_chat_sources([f"{flat}::chunk0"])
    assert len(sources) == 1
    assert sources[0].okf_path == okf
    assert sources[0].source_path == "RAW_DATA/Доклады/Доклад_Вострикова Н.М.pdf"
    assert sources[0].filename == "Доклад_Вострикова Н.М.pdf"


def test_resolve_chat_sources_dedupes_chunks_and_caps(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_service.wiki, "get_document_content", lambda _p: None
    )
    doc = "RAW_DATA/Обзоры/paper.pdf"
    sources = chat_service._resolve_chat_sources(
        [f"{doc}::chunk0", f"{doc}::chunk1", "paper-002"]
    )
    assert len(sources) == 2
    assert sources[0].doc_id == f"{doc}::chunk0"
    assert sources[1].doc_id == "paper-002"
    assert sources[1].okf_path is None


def test_resolve_chat_sources_links_bare_filenames(monkeypatch) -> None:
    okf = (
        "01_docling_clean00/RAW_DATA/Журналы/Цветные металлы/2018-все/"
        "ЦМ №6-2018.pdf.md"
    )

    class _Result:
        okf_path = okf

    class _SearchResponse:
        results = [_Result()]

    monkeypatch.setattr(
        chat_service.wiki, "get_document_content", lambda _p: None
    )
    monkeypatch.setattr(
        chat_service.wiki, "search_documents", lambda _q, limit=20: _SearchResponse()
    )
    monkeypatch.setattr(
        chat_service.wiki,
        "okf_to_raw_path",
        lambda _p: "RAW_DATA/Журналы/Цветные металлы/2018-все/ЦМ №6-2018.pdf",
    )

    sources = chat_service._resolve_chat_sources(["ЦМ №6-2018.pdf"])
    assert len(sources) == 1
    assert sources[0].okf_path == okf
    assert sources[0].filename == "ЦМ №6-2018.pdf"
