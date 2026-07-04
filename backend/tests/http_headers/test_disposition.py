from app.http_headers.disposition import attachment_content_disposition


def test_attachment_content_disposition_ascii() -> None:
    header = attachment_content_disposition("paper.pdf.md")
    assert header == 'attachment; filename="paper.pdf.md"'


def test_attachment_content_disposition_unicode() -> None:
    filename = "Copyright_letter_Письмо об авторском праве (на материалы презентации).pdf.md"
    header = attachment_content_disposition(filename)
    assert header.startswith('attachment; filename="Copyright_letter_')
    assert "filename*=UTF-8''" in header
    assert "%D0%9F%D0%B8%D1%81%D1%8C%D0%BC%D0%BE" in header
