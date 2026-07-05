# Postmortem: Files-Tree API Contract Drift

**Date**: 2026-07-03  
**Status**: Final  
**Incident Severity**: SEV3 (design/spec quality incident)  
**Incident Duration**: ~2 hours of iterative spec changes

## Executive Summary

During rapid documentation development, the `GET /api/v1/files/tree` contract changed multiple times without a single consolidated acceptance contract. This created contradictions across docs for path safety, subtree behavior, pagination, and visibility rules.

The issue was resolved by a focused review ("grilling") and explicit decision capture, then syncing `docs/LAYER_PRESENTATION.md` and `docs/SPECIFICATION.md` to a single final contract.

**Impact**:

- Elevated implementation risk for `service/main`.
- High chance of behavior mismatch between API and operator expectations.
- Rework overhead in docs and likely rework risk in code implementation.
- No production outage or data loss.

## Timeline (UTC+3)

| Time | Event |
|------|-------|
| ~02:25 | Initial architecture/spec docs review begins. |
| ~03:14 | Files-tree endpoint introduced into docs. |
| ~03:15 | Pagination (`offset`, `limit`) added. |
| ~03:18 | Subtree support clarified with implicit `SHARED` root. |
| ~03:20 | Focused critical review mode applied on `docs/`. |
| ~03:21-03:25 | Interactive Q/A decisions finalized (boundary, warnings, bounds, hidden/lock files, symlink policy). |
| ~03:26 | Docs aligned to final contract and pushed. |

## Root Cause Analysis

### What Happened

Contract details for the tree endpoint were expanded incrementally under time pressure. Each change solved a local concern but introduced or left unresolved edge-case ambiguity.

### Why It Happened

1. **Proximate cause**: Endpoint spec evolved without a frozen acceptance matrix before edits.
2. **Contributing factors**:
   - Multiple docs were updated in sequence, not as one lock-step contract update.
   - No checklist existed for filesystem-safety endpoints (path traversal, symlink behavior, hidden/system files, bounds).
   - "Best-effort" system philosophy encouraged permissive behavior, but escape/security boundaries still needed strict exceptions.

### 5 Whys

1. Why was the contract inconsistent?  
   -> Requirements were decided while writing docs, not before.
2. Why were requirements decided late?  
   -> Interactive discovery happened across many turns without a consolidation checkpoint.
3. Why no consolidation checkpoint?  
   -> No standard "contract freeze" step in current doc workflow.
4. Why no standard step?  
   -> Team process is optimized for speed, not formal API contract hardening.
5. Why optimized for speed?  
   -> Early stage project with heavy design churn and no enforcement template for spec completeness.

## Detection

### What Worked

- Critical interactive review surfaced contradictions quickly.
- One-by-one decision process forced precise choices on ambiguous behavior.
- Repeated doc sync reduced hidden divergences.

### What Didn't Work

- No pre-defined endpoint-spec checklist.
- No acceptance-test section per endpoint from the start.
- Some contract semantics were captured late (warnings shape, bounds, traversal policy).

## Response

### What Worked

- Blunt risk-first review.
- Interactive closure of unresolved decisions.
- Fast doc updates and push cadence.

### What Could Be Improved

- Introduce a contract template before endpoint drafting.
- Require a single "final decision table" section before implementation starts.
- Track unresolved items explicitly as "Open Decisions" instead of silent ambiguity.

## Impact

### Engineering Impact

- Additional review/edit cycles across multiple docs.
- Potential implementation delays avoided by clarifying now.

### Product/Operations Impact

- No runtime impact (docs-only incident).
- Reduced future ambiguity for admin panel and API consumers.

## Final Agreed Contract (Tree Endpoint)

1. `SHARED` is implicit root.
2. Subtree requests are relative to `SHARED`.
3. Recoverable malformed roots are normalized and returned with `200` + `warnings`.
4. Requests attempting to resolve outside `SHARED` return `400`.
5. Pagination stays v1 root-level: `offset`/`limit` apply to direct children only.
6. Bounds: `limit <= 1000`, `max_depth <= 10`.
7. Hidden files excluded.
8. Lock files excluded always (no override).
9. Symlinks are not followed.
10. Warning payload is structured objects (`code`, `message`).

## Lessons Learned

### What Went Well

- Interactive decision capture prevented unresolved ambiguity from shipping.
- Contract now includes explicit safety and scale boundaries.

### What Went Wrong

- Endpoint contract entered docs before core edge-case rules were frozen.
- "Known limitation" was identified only after review pressure.

### Where We Got Lucky

- This was caught before implementation was locked.
- Decision-maker was available for rapid interactive clarification.

## Action Items

| Priority | Action | Owner | Due |
|----------|--------|-------|-----|
| P0 | Add endpoint-spec checklist template (safety, bounds, pagination, visibility, warnings) to docs workflow | Team | Next docs iteration |
| P0 | Add "Open Decisions" subsection for every new endpoint until contract is final | Team | Immediate |
| P1 | Add acceptance-test bullets for each endpoint in `docs/LAYER_PRESENTATION.md` | Team | Before implementation phase |
| P1 | Add deterministic normalization rules examples for tree endpoint warnings | Team | Next refinement |
| P2 | Evaluate cursor-based deep pagination for future v2 tree endpoint | Team | Backlog |

## Appendix

Affected documents:

- `docs/LAYER_PRESENTATION.md`
- `docs/SPECIFICATION.md`
- `docs/LAYER_SERVICES.md`

Related process context:

- Interactive critical review mode with iterative decision capture on 2026-07-03.
