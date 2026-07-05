# -*- coding: utf-8 -*-
"""
Парсинг документов корпуса → текст с локаторами (стадия A конвейера).

Лёгкие парсеры без тяжёлых зависимостей: DOCX/DOCM (zipfile + XML),
PDF с текстовым слоем (pypdf). Более качественный бэкенд (Docling) подключается
той же сигнатурой parse_document() без изменения остального конвейера.

Выход — ParsedDoc: последовательность блоков {locator_kind, locator, text}
+ sha256 полного текста (артефакт для relocate-провенанса).
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_W_P = re.compile(r"</w:p>")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


@dataclass
class Block:
    locator_kind: str          # docx_para | pdf_page
    locator: str               # para:12 | p3
    text: str


@dataclass
class ParsedDoc:
    source_path: str
    blocks: list[Block] = field(default_factory=list)
    artifact_sha256: str = ""

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    def chunks(self, max_chars: int = 6000, overlap: int = 1) -> list[list[Block]]:
        """Чанки для LLM: подряд идущие блоки суммарно <= max_chars,
        с перекрытием в overlap блоков (чтобы факт на границе не потерялся)."""
        out: list[list[Block]] = []
        cur: list[Block] = []
        size = 0
        for b in self.blocks:
            if size + len(b.text) > max_chars and cur:
                out.append(cur)
                cur = cur[-overlap:] if overlap else []
                size = sum(len(x.text) for x in cur)
            cur.append(b)
            size += len(b.text)
        if cur:
            out.append(cur)
        return out


def parse_docx(path: Path) -> ParsedDoc:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "ignore")
    doc = ParsedDoc(source_path=str(path))
    for i, para in enumerate(_W_P.split(xml)):
        text = _WS.sub(" ", _TAGS.sub("", para)).strip()
        if len(text) < 3:
            continue
        doc.blocks.append(Block("docx_para", f"para:{i}", text))
    doc.artifact_sha256 = hashlib.sha256(doc.full_text.encode()).hexdigest()
    return doc


def parse_pdf(path: Path) -> ParsedDoc:
    from pypdf import PdfReader
    doc = ParsedDoc(source_path=str(path))
    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages, start=1):
        text = _WS.sub(" ", (page.extract_text() or "")).strip()
        if len(text) < 10:
            continue
        doc.blocks.append(Block("pdf_page", f"p{i}", text))
    doc.artifact_sha256 = hashlib.sha256(doc.full_text.encode()).hexdigest()
    return doc


def parse_markdown_text(text: str, source_path: str = "") -> ParsedDoc:
    """OKF raw markdown (текст) → блоки-параграфы. Общее ядро для файлового
    parse_markdown и HTTP-ингеста из parser SHARED (ingest_shared.py)."""
    doc = ParsedDoc(source_path=source_path)
    for i, para in enumerate(re.split(r"\n\s*\n", text)):
        clean = _WS.sub(" ", para.replace("\n", " ")).strip()
        if len(clean) < 3 or clean.startswith(("---", "```")):
            continue
        doc.blocks.append(Block("md_para", f"para:{i}", clean))
    doc.artifact_sha256 = hashlib.sha256(doc.full_text.encode()).hexdigest()
    return doc


def parse_markdown(path: Path) -> ParsedDoc:
    """OKF raw markdown (выход docling из ingest-контура) → блоки-параграфы."""
    return parse_markdown_text(path.read_text(encoding="utf-8", errors="ignore"),
                               str(path))


PARSERS = {".docx": parse_docx, ".docm": parse_docx, ".pdf": parse_pdf,
           ".md": parse_markdown, ".markdown": parse_markdown}


def parse_document(path: str | Path) -> ParsedDoc:
    path = Path(path)
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"нет парсера для {path.suffix}: {path.name}")
    return parser(path)


def detect_lang(text: str) -> str:
    """ru/en по доле кириллицы (для Document.lang и фильтра практики)."""
    cyr = sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text[:4000])
    letters = sum(ch.isalpha() for ch in text[:4000]) or 1
    return "ru" if cyr / letters > 0.4 else "en"
