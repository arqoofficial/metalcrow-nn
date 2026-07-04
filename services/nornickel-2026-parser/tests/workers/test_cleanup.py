"""Cleanup worker tests."""

from app.workers.cleanup import CLEANER_VERSION, clean_docling_markdown


def test_cleaner_version_is_set() -> None:
    assert CLEANER_VERSION


def test_clean_docling_markdown_removes_repeated_page_footer() -> None:
    text = "\n".join(
        [
            "Report title",
            "",
            "Body paragraph one continues",
            "on the next visual line.",
            "",
            "Page 1",
            "\f",
            "Report title",
            "",
            "Second page content here.",
            "",
            "Page 2",
        ]
    )
    cleaned = clean_docling_markdown(text)
    assert "Page 1" not in cleaned
    assert "Page 2" not in cleaned
    assert "Body paragraph one continues on the next visual line." in cleaned.replace("\n", " ")


def test_clean_docling_markdown_preserves_headings() -> None:
    text = "# Title\n\nParagraph text.\n"
    assert "# Title" in clean_docling_markdown(text)
