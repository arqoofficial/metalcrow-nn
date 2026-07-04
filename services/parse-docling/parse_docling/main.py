from tool_sdk import InvokeRequest, InvokeResponse, ToolManifest, create_tool_app

from parse_docling.config import settings
from parse_docling.tasks import parse_document

MANIFEST = ToolManifest(
    name="parse_docling",
    description="PDF/DOC/XLSX -> raw Markdown (L1, Docling)",
    version=settings.SERVICE_VERSION,
    queue="parse.docling",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "document_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["task_id", "document_ids"],
    },
    output_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
    },
    degraded_behavior="fail when parser pipeline does not complete",
    reads=["postgres", "nornickel-parser"],
    writes=["postgres"],
    deps=["nornickel-parser"],
)


def invoke_handler(request: InvokeRequest) -> InvokeResponse:
    task_id = str(request.params["task_id"])
    document_ids = [str(x) for x in request.params["document_ids"]]
    parse_document.delay(task_id, document_ids)
    return InvokeResponse(
        ok=True,
        tool=MANIFEST.name,
        result={"task_id": task_id, "queued": True},
    )


app = create_tool_app(
    name=settings.SERVICE_NAME,
    version=settings.SERVICE_VERSION,
    manifest=MANIFEST,
    invoke_handler=invoke_handler,
)
