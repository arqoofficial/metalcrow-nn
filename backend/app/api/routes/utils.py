from fastapi import APIRouter, Depends
from pydantic.networks import EmailStr

from app.api.deps import get_current_active_superuser
from app.core.config import settings
from app.models import Message
from app.services import llm
from app.utils import generate_test_email, send_email

router = APIRouter(prefix="/utils", tags=["utils"])


@router.post(
    "/test-email/",
    dependencies=[Depends(get_current_active_superuser)],
    status_code=201,
)
def test_email(email_to: EmailStr) -> Message:
    """
    Test emails.
    """
    email_data = generate_test_email(email_to=email_to)
    send_email(
        email_to=email_to,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Test email sent")


@router.get("/health-check/")
async def health_check() -> bool:
    return True


@router.get("/llm-health", dependencies=[Depends(get_current_active_superuser)])
def llm_health() -> dict[str, object]:
    """Real minimal gateway round-trip so a misconfigured/unreachable LLM is
    visible, not silent (spec §2.7). Superuser-gated (M4 fix, matching the
    other admin/diagnostic routes in this module): unauthenticated, this
    fires a real paid gateway completion per request, i.e. an anonymous
    caller could hit it in a loop and run up gateway spend/DoS the LLM
    backend — the round-trip itself stays real, only the auth gate is new."""
    result = llm.chat([{"role": "user", "content": "ping"}])
    return {"ok": result.ok, "model": settings.LITSEARCH_LLM_MODEL}
