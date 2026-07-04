"""Wiki document browser over parser SHARED/01_docling_clean00/."""

from __future__ import annotations

from app.schemas.wiki import (
    WIKI_STAGE_ROOT,
    WikiDocumentContent,
    WikiFileTreeNode,
    WikiSearchResponse,
    WikiSearchResult,
    WikiTreeResponse,
)
from app.services import parser_client


def okf_to_raw_path(okf_path: str) -> str:
    normalized = okf_path.strip("/")
    if not normalized.endswith(".md"):
        raise ValueError("OKF path must end with .md")
    without_md = normalized[: -len(".md")]
    for prefix in ("01_docling_clean00/", "00_docling_raw/"):
        if without_md.startswith(prefix):
            return without_md[len(prefix) :]
    return without_md


def _document_title(okf_path: str) -> str:
    return okf_path.rsplit("/", 1)[-1]


def _find_subtree(node: parser_client.FileTreeNode, path: str) -> parser_client.FileTreeNode:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return node

    current = node
    idx = 0
    if current.name == parts[0]:
        idx = 1

    while idx < len(parts):
        part = parts[idx]
        if current.name == part:
            idx += 1
            continue
        child = next((item for item in current.children if item.name == part), None)
        if child is None:
            break
        current = child
        idx += 1
    return current


def _relative_to_stage_root(path: str) -> str:
    prefix = f"{WIKI_STAGE_ROOT}/"
    return path.removeprefix(prefix) if path.startswith(prefix) else path


def _annotate_tree_paths(
    node: parser_client.FileTreeNode,
    *,
    parent_path: str,
) -> WikiFileTreeNode:
    current_path = f"{parent_path}/{node.name}" if parent_path else node.name
    if node.type == "file":
        return WikiFileTreeNode(name=node.name, type="file", path=current_path)
    children = [
        _annotate_tree_paths(child, parent_path=current_path) for child in node.children
    ]
    return WikiFileTreeNode(
        name=node.name,
        type="dir",
        path=None,
        children=children,
    )


def get_tree(
    *,
    root: str = WIKI_STAGE_ROOT,
    max_depth: int = 10,
) -> WikiTreeResponse:
    tree_response = parser_client.fetch_tree(
        root=root,
        max_depth=max_depth,
        include_files=True,
        include_dirs=True,
    )
    stage_root = tree_response.resolved_root or root
    root_node = _find_subtree(tree_response.tree, stage_root)
    children = [
        _annotate_tree_paths(child, parent_path=WIKI_STAGE_ROOT)
        for child in root_node.children
    ]
    return WikiTreeResponse(
        requested_root=tree_response.requested_root,
        resolved_root=stage_root,
        generated_at=tree_response.generated_at,
        children=children,
    )


def _collect_md_files(
    node: WikiFileTreeNode,
    *,
    query: str,
    results: list[WikiSearchResult],
    limit: int,
) -> None:
    if len(results) >= limit:
        return
    if node.type == "file" and node.path and node.path.endswith(".md"):
        title = _document_title(node.path)
        haystack = f"{title} {node.path}".lower()
        if query in haystack:
            results.append(
                WikiSearchResult(
                    okf_path=node.path,
                    title=title,
                    snippet=_relative_to_stage_root(node.path),
                )
            )
        return
    for child in node.children:
        _collect_md_files(child, query=query, results=results, limit=limit)
        if len(results) >= limit:
            return


def search_documents(q: str, *, limit: int = 50) -> WikiSearchResponse:
    query = q.strip().lower()
    if not query:
        return WikiSearchResponse(results=[], total=0)

    tree = get_tree()
    results: list[WikiSearchResult] = []
    for child in tree.children:
        _collect_md_files(child, query=query, results=results, limit=limit)
        if len(results) >= limit:
            break
    return WikiSearchResponse(results=results, total=len(results))


def get_document_content(okf_path: str) -> WikiDocumentContent | None:
    normalized = okf_path.strip("/")
    if not normalized.startswith(WIKI_STAGE_ROOT):
        normalized = f"{WIKI_STAGE_ROOT}/{normalized.removeprefix('/')}"

    try:
        markdown = parser_client.fetch_markdown(normalized)
    except parser_client.ParserError:
        return None

    raw_path: str | None
    try:
        raw_path = okf_to_raw_path(normalized)
    except ValueError:
        raw_path = None

    return WikiDocumentContent(
        okf_path=normalized,
        title=_document_title(normalized),
        raw_path=raw_path,
        markdown=markdown,
        display_path=_relative_to_stage_root(normalized),
    )


def fetch_document_markdown_download(okf_path: str) -> tuple[str, str]:
    normalized = okf_path.strip("/")
    if not normalized.startswith(WIKI_STAGE_ROOT):
        normalized = f"{WIKI_STAGE_ROOT}/{normalized.removeprefix('/')}"
    markdown = parser_client.fetch_markdown(normalized)
    return _document_title(normalized), markdown


def fetch_document_raw_download(okf_path: str) -> parser_client.RawFileResponse:
    normalized = okf_path.strip("/")
    if not normalized.startswith(WIKI_STAGE_ROOT):
        normalized = f"{WIKI_STAGE_ROOT}/{normalized.removeprefix('/')}"
    raw_path = okf_to_raw_path(normalized)
    return parser_client.fetch_raw(raw_path)
