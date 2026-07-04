# -*- coding: utf-8 -*-
"""Тесты работают в отдельной БД onto_test — reset схемы не трогает дев-данные."""
import os

# всегда принудительно: тесты не должны попасть в дев-базу ни при каком env
os.environ["ONTOLOGY_DB_URL"] = (
    "postgresql://postgres:onto@localhost:56543/onto_test")
