"""Reproduce the bounded Обзоры review sample → cleaned markdown.

OSN green-lit a bounded 15-doc sample from the task-2 corpus's ``Обзоры``
(reviews) folder for corpus glossary mining. Every sampled doc turned out to be
born-digital, so text is extracted **on-VM** (no cloud egress):

  * ``.docx`` — read ``word/document.xml`` directly, dropping Word field
    instruction codes (``<w:instrText>`` PAGEREF/REF/TOC…) which otherwise leak
    junk tokens like ``"никеля pageref"``, and unescaping HTML entities.
  * ``.pdf``  — PyMuPDF text layer (all sampled PDFs are born-digital;
    genuinely scanned / table-dense pages are the only ones worth routing to the
    NuExtract vision API, where it earns its cost).

Zip entry names are CP866-encoded (Russian Windows), recovered via
``cp437 -> cp866``.

Usage::

    python scripts/parse_reviews_sample.py \
        --zip /path/to/corpus.zip --out-dir data/corpus_sample

Then build the glossary over the sample::

    uv run python cli.py build --doc-dir data/corpus_sample --out-dir out_corpus
"""

from __future__ import annotations

import html
import logging
import re
import zipfile
from pathlib import Path

import click

logger = logging.getLogger(__name__)

REVIEW_FOLDER = "Задача 2. Научный клубок/Источники информации/Обзоры/"

# The curated, term-dense sample: specialized unit-ops / equipment / materials
# that Wikidata + Wikipedia have no RU label for.
SAMPLE_FILES = [
    "Обеднение_шлаков.docx",
    "ОИП-05-2019 Параметры Cu EW.docx",
    "Хлорное выщелачивание ОИП 02-2024.docx",
    "ТИ. Печи КС.docx",
    "ОИП-03-2022 Рудоподготовка дробление-измельчение.docx",
    "Обзор пеработка медно-никелевых штейнов (обжиг-выщелачивание) фул.docx",
    "Цианидное выщелачивание МПГ.docx",
    "ОИП-01-2022 Обзор существующих технологий получения сульфатов никеля и кобальта.docx",
    "Огнеупорные материалы, используемые для футеровки металлургических печей медного производства.docx",
    "Обзор технических решений в области электролитического производства никеля и меди.docx",
    "Сверхтонкое_измельчение_2012.pdf",
    "ОИ - 1 - 2016  Диспергирование.pdf",
    "Мельницы_июль 12.pdf",
    "ОИ - 2 - 2016  Извлечение благородных металлов из шламов и шлаков металлургического производсва.pdf",
    "Медные порошки.pdf",
]


def _fix_name(name: str) -> str:
    """Recover a CP866 zip entry name mangled through CP437."""
    try:
        return name.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def docx_text(data: bytes) -> str:
    """Extract plain text from a .docx byte blob, dropping Word field codes."""
    import io

    xml = zipfile.ZipFile(io.BytesIO(data)).read(
        "word/document.xml").decode("utf-8", "ignore")
    # Field instruction runs (PAGEREF/REF/TOC/HYPERLINK) are not document text.
    xml = re.sub(r"<w:instrText[^>]*>.*?</w:instrText>", " ", xml, flags=re.S)
    xml = re.sub(r'<w:fldSimple[^>]*w:instr="[^"]*"[^>]*>', "", xml)
    xml = xml.replace("</w:p>", "\n").replace("</w:tr>", "\n")
    txt = html.unescape(re.sub(r"<[^>]+>", "", xml))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", txt)).strip()


def pdf_text(data: bytes) -> str:
    """Extract the text layer from a born-digital PDF byte blob."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    return "\n\n".join(page.get_text("text") for page in doc).strip()


@click.command()
@click.option("--zip", "zip_path", required=True, type=click.Path(exists=True),
              help="Path to the task-2 corpus.zip.")
@click.option("--out-dir", default="data/corpus_sample", show_default=True,
              type=click.Path())
def main(zip_path: str, out_dir: str) -> None:
    """Extract the bounded review sample from CORPUS.ZIP to OUT_DIR as markdown."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    zf = zipfile.ZipFile(zip_path)
    by_name = {_fix_name(i.filename)[len(REVIEW_FOLDER):]: i
               for i in zf.infolist()
               if _fix_name(i.filename).startswith(REVIEW_FOLDER)}

    n_ok = 0
    for idx, want in enumerate(SAMPLE_FILES):
        info = by_name.get(want)
        if info is None:
            logger.warning("Sample file not found in zip: %s", want)
            continue
        data = zf.read(info.filename)
        ext = want.rsplit(".", 1)[-1].lower()
        text = docx_text(data) if ext == "docx" else pdf_text(data)
        dst = out / f"rev{idx:02d}.md"
        dst.write_text(f"# {want}\n\n{text}\n", encoding="utf-8")
        n_ok += 1
        logger.info("%s -> %s (%d chars)", want, dst.name, len(text))

    logger.info("Parsed %d/%d review docs into %s",
                n_ok, len(SAMPLE_FILES), out)


if __name__ == "__main__":
    main()
