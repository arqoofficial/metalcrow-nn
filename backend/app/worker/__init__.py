"""Celery worker package for the litsearch → chat integration pipeline
(spec §2.4) — separate from the SPEC_V5 ingestion-plane workers under
`services/*/`, this one runs in-process against `backend/app`'s own models
and DB engine since `reconcile`/`agent_continue` are plain ORM/SQL + LLM-loop
operations, not a standalone microservice.
"""
