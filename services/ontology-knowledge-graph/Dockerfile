# ontology-knowledge-graph — типизированная онтология поверх Postgres.
# Internal-only sidecar (без публичного порта), вызывается backend'ом по HTTP
# (ONTOLOGY_KG_URL) — та же схема интеграции, что у science-knowledge-graph.
# Стек без torch/spaCy: эмбеддинги — через fastembed (ONNX-runtime), pip достаточно.
FROM python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR /app/

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Модель эмбеддингов запекается в образ (скачивание на этапе сборки) — рантайм
# оффлайн и быстрый; иначе первый запрос ждёт ~1 ГБ загрузки.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-mpnet-base-v2')"

COPY ontology /app/ontology

EXPOSE 8000

# service_init: создать БД (если нет) + DDL + сиды + автозагрузка батчей
CMD ["sh", "-c", "python -m ontology.service_init && uvicorn ontology.tool_service:app --host 0.0.0.0 --port 8000"]
