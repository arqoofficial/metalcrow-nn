# Docling conversion

Docling is the **core** stage-0 conversion engine for this project. Every supported raw document is converted to markdown through the real [Docling](https://docling-project.github.io/docling/) library — not a stub, not optional.

Related: [SPECIFICATION.md](SPECIFICATION.md), [LAYER_SERVICES.md](LAYER_SERVICES.md), [LAYER_DATA.md](LAYER_DATA.md), [LAYER_CONFIG.md](LAYER_CONFIG.md), [FAST_CLEANUP_EXAMPLE.md](FAST_CLEANUP_EXAMPLE.md).

---

## 1. Role in the pipeline

| Stage | Service | Conversion |
|-------|---------|------------|
| 0 `docling_raw` | `service/raw2docling_raw` | Raw file → Docling → OKF markdown in `00_docling_raw/` |
| 1 `docling_clean00` | `service/docling_raw2docling_clean00` | Fast cleanup on body → `01_docling_clean00/` |

Stage 0 **must** produce substantive markdown. Placeholder or empty output is treated as a conversion failure.

---

## 2. Dependency (required)

Docling and EasyOCR are **main** dependencies in `pyproject.toml`:

```bash
uv sync
```

There is no `--extra workers` profile. If `docling` is not installed, the application and test suite fail at import or startup.

Docker images run:

```bash
uv sync --frozen --no-dev --no-install-project
```

System packages in Dockerfiles: `libgl1`, `libglib2.0-0` (Docling / OpenCV runtime).

---

## 3. Implementation

| Module | Responsibility |
|--------|----------------|
| `app/workers/docling.py` | `DocumentConverter`, OCR options, `convert_raw_to_markdown()`, output validation |
| `app/workers/stage0.py` | Worker lock, OKF frontmatter, enqueue stage 1 |
| `app/workers/cleanup.py` | Stage-1 markdown cleanup (`CLEANER_VERSION`) |
| `app/paths.py` | `DOCLING_INPUT_EXTENSIONS`, `ARCHIVE_EXTENSIONS`, `is_docling_input_path()` |

### Conversion flow

1. Verify path is Docling-eligible (`is_docling_input_path`).
2. Build `DocumentConverter` with `PdfFormatOption` when OCR is enabled for PDFs.
3. `converter.convert(path)` — require status `success` or `partial_success`.
4. `result.document.export_to_markdown()`.
5. `validate_substantive_markdown()` — reject stubs and short output.
6. Return markdown with trailing newline.

### Output validation

`validate_substantive_markdown()` rejects:

- Empty body
- Stub signatures: `Converted from`, `without OCR`, `# Parsed PDF`, `# Parsed Document`, `Test conversion body`
- Fewer than **200** alphanumeric characters in the full document
- Title-only output (no substantive body beyond the first heading)

Failed validation raises `ValueError`; workers record a failure marker (see [LAYER_SERVICES.md](LAYER_SERVICES.md)).

### OCR

Default config (`config.yaml` → `pipeline.docling`):

```yaml
pipeline:
  docling:
    ocr_enabled: true
    ocr_languages: [en, ru]
```

Text-native PDFs and office formats use embedded text when available; OCR applies when Docling needs it for scanned pages.

### GPU acceleration (optional)

Docling EasyOCR path supports GPU via `EasyOcrOptions(use_gpu=...)`.

Runtime mode is controlled by worker env `DOC_OCR_USE_GPU`:

- `auto` (default): detect CUDA with `torch.cuda.is_available()`
- `true`: require GPU, fail fast when CUDA is unavailable
- `false`: force CPU

Host/container prerequisites for GPU:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Enable GPU pass-through for workers:

```bash
DOCKER_GPU_COUNT=1 docker compose up -d raw2docling_raw docling_raw2docling_clean00
```

CPU fallback remains automatic in `auto` mode when GPU is unavailable.

---

## 4. Offline model preload (required for offline workers)

Workers can be configured to require preloaded model caches (`REQUIRE_PRELOADED_MODELS=true`).
In this mode, worker startup/conversion **must not download** OCR/Docling models.

### Cache location

Worker containers use:

- `MODEL_CACHE_ROOT=/models`
- `EASYOCR_MODULE_PATH=/models/easyocr`
- `HF_HOME=/models/huggingface`
- `TRANSFORMERS_CACHE=/models/huggingface/transformers`
- `TORCH_HOME=/models/torch`
- `XDG_CACHE_HOME=/models/xdg`

Compose mounts `./SHARED/MODELS:/models` for worker services.

### Preload command

Run once (with internet access) before offline startup:

```bash
./rerun.sh preload-models
```

Equivalent direct command:

```bash
docker compose run --rm raw2docling_raw python scripts/preload_models.py
```

### Verification

Check cache readiness without downloads:

```bash
python scripts/preload_models.py --check-only
```

If sentinel/cache files are missing, workers fail fast with a clear error instead of silently fetching from the network.

---

## 5. Supported input types

Docling-eligible extensions (from `app/paths.py` → `DOCLING_INPUT_EXTENSIONS`):

`.pdf`, `.docx`, `.pptx`, `.html`, `.htm`, `.md`, `.csv`, `.xlsx`, `.odt`, `.ods`, `.odp`, `.asciidoc`, `.adoc`, `.epub`, `.latex`, `.tex`, `.vtt`

### Excluded from pipeline

**Archives** and legacy/unlisted types are **not** processed:

- Archives: `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, … (`ARCHIVE_EXTENSIONS`)
- Legacy spreadsheets: `.xls` (not in Docling set)
- Upload, reindex, process, and status APIs return `400`/`404` for ineligible paths

Reindex and statistics only count Docling-eligible raw files under `UPLOAD_DATA/` and `RAW_DATA/`.

---

## 6. OKF metadata (stage 0)

Stage-0 frontmatter written by `run_stage0_job()`:

| Field | Source |
|-------|--------|
| `pipeline.docling_version` | Installed Docling package version |
| `pipeline.worker` | `raw2docling_raw` |
| `raw.media_type` | MIME map from file extension |
| `git.commit` / `git.version_label` | `GIT_COMMIT` / `GIT_VERSION_LABEL` env or `git rev-parse` |

Stage-1 adds `pipeline.cleaner_version` and `pipeline.worker: docling_raw2docling_clean00`.

---

## 7. Testing

Docling tests are **mandatory** in every `pytest` run.

### Corpus

Tests discover real PDFs under `SHARED/RAW_DATA/` (gitignored on developer machines with data):

- `tests/raw_data_samples.py` — fails at import if the directory is missing or contains no PDFs
- `tests/workers/test_docling_integration.py` — converts the **3 smallest** PDFs; asserts substantive markdown
- Worker tests copy sample PDFs from `RAW_DATA` instead of fake byte payloads

```bash
uv sync
uv run pytest -q    # includes Docling integration; ~2–3 min with real conversion
```

### Unit tests

`tests/workers/test_docling.py` covers validation helpers, OCR config, and error paths without requiring a full corpus for every case.

---

## 8. Regenerating stale OKF outputs

OKF files produced by an older stub pipeline may still contain:

```yaml
pipeline:
  docling_version: stub
```

and body text like `Converted from ... without OCR`.

After deploying real Docling, rebuild and reindex:

```bash
docker compose build
docker compose up -d
./reindex.sh
# or POST /api/v1/reindex with {"enforce": true}
```

---

## 9. Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Added OCR GPU auto-detect (`DOC_OCR_USE_GPU`) with CPU fallback and compose GPU pass-through |
| 2026-07-03 | Added offline preload workflow and strict no-download worker guard |
| 2026-07-03 | Docling required core dependency; real conversion + validation; integration tests on `SHARED/RAW_DATA` PDFs |
| 2026-07-03 | Archive and non-Docling types excluded from upload/reindex/process |
| 2026-07-03 | OCR defaults `en` + `ru`; replaced stub converter |
