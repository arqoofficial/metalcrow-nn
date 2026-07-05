# deploy/ — ontology database snapshot

`ontology_snapshot.dump` is a compressed `pg_dump` (custom format) of the
`ontology` database, stored via **Git LFS** (~85 MB, includes the passage
embeddings). It captures the full extracted ontology base as a ready-to-serve
state.

## Why a snapshot and not just the batches

The ontology base is not shipped as rows in git — it is reconstructed at
runtime. `service_init` autoloads `ontology/batches/*.json` **only into an empty
DB**, and rebuilds passage embeddings in the background. A server whose ontology
volume was populated at an earlier point therefore keeps its old, smaller base
across redeploys, and its search can run embedding-degraded until the background
rebuild finishes.

This snapshot sidesteps both: it restores the exact captured facts **and** the
prebuilt embeddings, so a server matches the reference state 1:1 immediately.

## Restore on a server

```bash
git pull
git lfs pull                        # resolve the LFS-stored dump
./scripts/restore-ontology-dump.sh --prod
```

The batches under `services/ontology-knowledge-graph/ontology/batches/` remain
the reproducible source for a from-scratch rebuild; this snapshot is the
deterministic delivery of a known-good state.
