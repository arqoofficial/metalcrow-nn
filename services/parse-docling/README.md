# Slot for colleague's Docling microservice (SPEC_V5 §4).
#
# Contract: packages/tool_sdk (`/health`, `/manifest`, `/invoke`)
# Queue: `parse.docling`
# Celery task: `parse.docling.parse`
#
# Replace stub markdown in `parse_docling/db.py::bytes_to_markdown_stub`
# with real Docling conversion when ready.
