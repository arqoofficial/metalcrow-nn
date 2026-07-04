"""Pydantic-модели датасета и результатов прогона.

Датасет (`data/questions.yaml`) — список `CompetenceQuestion`. Каждый вопрос
несёт `gold`-эталон (что должно быть в правильном ответе) и «мягкие» ожидания
маршрутизации (`expected_mode` / `expected_tools_any`). Прогон превращает
каждый вопрос в `QuestionResult`, а весь запуск — в `RunReport`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Lang(StrEnum):
    RU = "ru"
    EN = "en"


class ChatMode(StrEnum):
    AUTO = "auto"
    ONTOLOGY = "ontology"
    KNOWLEDGE_GRAPH = "knowledge_graph"


class Op(StrEnum):
    EQ = "="
    LE = "<="
    GE = ">="
    APPROX = "~"
    RANGE = "range"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ExpectedValue(BaseModel):
    """Числовой факт, который должен присутствовать в полном ответе.

    `op`:
      `=`/`~` — значение примерно равно `value` (в пределах `tol`);
      `<=`/`>=` — в ответе фигурирует `value` как граница;
      `range` — значение лежит в [`value`, `value2`] (либо обе границы названы).
    """

    label: str = ""
    value: float
    op: Op = Op.APPROX
    value2: float | None = None
    unit: str = ""
    tol: float = 0.0


class GoldSpec(BaseModel):
    """Эталон правильного ответа + пороги провенанса."""

    must_include_any: list[str] = Field(default_factory=list)
    must_include_all: list[str] = Field(default_factory=list)
    forbid_include: list[str] = Field(default_factory=list)
    expected_values: list[ExpectedValue] = Field(default_factory=list)
    expected_sources: list[str] = Field(default_factory=list)
    expects_patent: bool = False
    patent_numbers: list[str] = Field(default_factory=list)
    min_citations: int = 0
    min_numeric_values: int = 0
    # honesty: для неотвечаемых (answerable=false) вопросов ответ должен честно
    # сообщить об отсутствии данных, а не выдумать факты.
    expect_no_data: bool = False


# Плоские ключи workflow-генератора → вложенный gold. Позволяет класть в YAML
# как вложенный `gold:`, так и плоские поля рядом с вопросом.
_GOLD_FIELDS = set(GoldSpec.model_fields)


class CompetenceQuestion(BaseModel):
    id: str
    lang: Lang = Lang.RU
    category: str
    question: str
    # «мягкие» ожидания маршрутизации (не жёсткий провал, влияют на mode-метрику)
    expected_mode: ChatMode = ChatMode.AUTO
    expected_tools_any: list[str] = Field(default_factory=list)
    # какой режим ОТПРАВЛЯТЬ в чат для этого вопроса; None = режим задаёт раннер
    ask_mode: ChatMode | None = None
    gold: GoldSpec = Field(default_factory=GoldSpec)
    answerable: bool = True
    difficulty: Difficulty = Difficulty.MEDIUM
    grounding_note: str = ""
    weight: float = 1.0

    @model_validator(mode="before")
    @classmethod
    def _lift_flat_gold(cls, data: object) -> object:
        """Принять плоскую форму (must_include_any/expected_values/… на верхнем
        уровне) и собрать из неё `gold`. Так выход workflow-генератора грузится
        без переформатирования."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        gold = dict(data.get("gold") or {})
        for key in list(data):
            if key in _GOLD_FIELDS:
                gold.setdefault(key, data.pop(key))
        if gold:
            data["gold"] = gold
        return data


# ── Результаты прогона ────────────────────────────────────────────────────


class MetricScore(BaseModel):
    name: str
    score: float  # 0..1
    weight: float
    detail: str = ""
    applicable: bool = True


class QuestionResult(BaseModel):
    id: str
    category: str
    question: str
    ask_mode: str
    lang: str = "ru"
    difficulty: str = "medium"
    answerable: bool = True
    # транспорт
    ok: bool = False
    error: str | None = None
    latency_s: float = 0.0
    # что вернул чат
    mode_used: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    answer_text: str = ""
    n_numeric: int = 0
    n_citations: int = 0
    n_experiment_ids: int = 0
    has_patent: bool = False
    # оценки
    weight: float = 1.0
    metrics: list[MetricScore] = Field(default_factory=list)
    score: float = 0.0
    judge: dict | None = None

    def metric(self, name: str) -> float | None:
        for m in self.metrics:
            if m.name == name and m.applicable:
                return m.score
        return None


class ComponentHealth(BaseModel):
    backend: bool = False
    ontology_kg: bool | None = None
    science_kg: bool | None = None
    detail: dict = Field(default_factory=dict)


class RunReport(BaseModel):
    started_at: str
    finished_at: str = ""
    base_url: str
    modes: list[str] = Field(default_factory=list)
    dataset_path: str = ""
    n_questions: int = 0
    components: ComponentHealth = Field(default_factory=ComponentHealth)
    results: list[QuestionResult] = Field(default_factory=list)
    overall: dict = Field(default_factory=dict)
