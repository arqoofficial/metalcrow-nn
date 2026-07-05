"""Progress mapping for long-running Docling polling."""

from parse_docling.parser_client import ProcessingStatus, poll_wait_fraction


def test_poll_wait_fraction_moves_while_processing() -> None:
    queued = poll_wait_fraction(30.0, 900.0, ProcessingStatus.queued)
    processing = poll_wait_fraction(30.0, 900.0, ProcessingStatus.processing)
    done = poll_wait_fraction(30.0, 900.0, ProcessingStatus.done)

    assert queued < processing
    assert done == 1.0


def test_poll_wait_fraction_never_exceeds_one() -> None:
    assert poll_wait_fraction(10_000.0, 900.0, ProcessingStatus.processing) <= 1.0


def test_timeout_error_mentions_workers_when_still_queued() -> None:
    from parse_docling.parser_client import FileStatusResponse, StageStatus, _timeout_error

    status = FileStatusResponse(
        requested_path="UPLOAD_DATA/x.pdf",
        resolved_path="UPLOAD_DATA/x.pdf",
        overall_status=ProcessingStatus.queued,
        stages=[
            StageStatus(stage="docling_raw", status=ProcessingStatus.queued, okf_path="a.md"),
        ],
    )
    message = str(_timeout_error("UPLOAD_DATA/x.pdf", status))
    assert "queued" in message
    assert "workers" in message


def test_enqueue_process_treats_409_as_already_done(monkeypatch) -> None:  # noqa: ANN001
    from parse_docling import parser_client

    class FakeResponse:
        status_code = 409
        text = '{"detail":"stage-0 output already exists"}'

    class FakeClient:
        def post(self, *args, **kwargs):  # noqa: ANN001, ARG002
            return FakeResponse()

        def __enter__(self):
            return self

        def __exit__(self, *args):  # noqa: ANN001
            return False

    monkeypatch.setattr(parser_client, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        parser_client,
        "get_status",
        lambda path: parser_client.FileStatusResponse(
            requested_path=path,
            resolved_path=path,
            overall_status=ProcessingStatus.done,
            stages=[
                parser_client.StageStatus(
                    stage="docling_clean00",
                    status=ProcessingStatus.done,
                    okf_path="01_docling_clean00/x.md",
                )
            ],
        ),
    )

    result = parser_client.enqueue_process("UPLOAD_DATA/x.pdf")
    assert result.status == ProcessingStatus.done
