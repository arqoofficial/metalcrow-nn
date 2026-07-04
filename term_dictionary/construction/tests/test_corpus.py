"""Markdown-scrubber + corpus-loader tests (no network, no model)."""

from pathlib import Path

from term_dict.corpus import ParsedDoc, corpus_texts, load_corpus, strip_markdown


def test_strip_markdown_removes_syntax_keeps_words():
    md = (
        "# Флотация никеля\n\n"
        "The **flash smelting furnace** (FSF) treats `feed` [see](http://x).\n\n"
        "```python\nignore_me = 1\n```\n\n"
        "| col | val |\n|-----|-----|\n| pH | 4.2 |\n\n"
        "- bullet item\n> quoted line\n"
    )
    out = strip_markdown(md)
    assert "Флотация никеля" in out
    assert "flash smelting furnace" in out
    assert "**" not in out and "#" not in out
    assert "ignore_me" not in out          # code fence dropped
    assert "http://x" not in out           # url dropped, link text kept
    assert "see" in out
    assert "bullet item" in out
    assert "quoted line" in out
    assert "pH" in out and "4.2" in out     # table cell content survives
    assert "-----" not in out               # table separator row gone


def test_load_corpus_reads_and_strips(tmp_path: Path):
    (tmp_path / "a.md").write_text("# Title\n**leaching** of ore.", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plain electrowinning text", encoding="utf-8")
    (tmp_path / "skip.pdf").write_text("nope", encoding="utf-8")
    docs = load_corpus(tmp_path)
    ids = {d.doc_id for d in docs}
    assert ids == {"a.md", "b.txt"}          # pdf ignored
    a = next(d for d in docs if d.doc_id == "a.md")
    assert "#" not in a.text and "leaching of ore" in a.text
    assert all(isinstance(d, ParsedDoc) and d.source for d in docs)


def test_load_corpus_max_files_caps(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"d{i}.md").write_text(f"term{i}", encoding="utf-8")
    docs = load_corpus(tmp_path, max_files=2)
    assert len(docs) == 2
    assert len(corpus_texts(docs)) == 2


def test_load_corpus_missing_dir_is_empty(tmp_path: Path):
    assert load_corpus(tmp_path / "nope") == []
