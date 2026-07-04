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
