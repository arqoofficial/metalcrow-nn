# -*- coding: utf-8 -*-
"""LLM-синтез финального ответа из найденных пассажей (generation в RAG).

Ретривер поднимает фрагменты; здесь LLM (gpt-oss-120b) читает вопрос + фрагменты
и пишет краткий связный ответ ТОЛЬКО по ним, с числами/единицами и ссылкой на
документ. Если фрагменты не отвечают на вопрос — возвращает сигнал «нет данных»
(это чинит и релевантность, и честность: левый фрагмент не превращается в ответ).

LLM недоступна / ошибка → None: вызывающий (mocks.agent) деградирует на сырые
пассажи, как раньше. Числа берутся ИЗ фрагментов, не выдумываются.
"""
from __future__ import annotations

from . import intent_llm

NO_DATA = "НЕТ ДАННЫХ"

_SYS = (
    "Ты — ассистент по базе знаний горно-металлургического R&D (Ni/Cu/МПГ: "
    "обогащение, пиро- и гидрометаллургия, вода, геомеханика). Отвечай на вопрос "
    "СТРОГО по приведённым фрагментам корпуса.\n"
    "Правила:\n"
    "1) Краткий связный ответ по-русски с конкретными числами и единицами, "
    "взятыми ИЗ фрагментов; ничего не добавляй сверх фрагментов и не выдумывай.\n"
    "2) Фрагменты из полнотекстового поиска, часть — про ДРУГИЕ вещества/процессы. "
    "Используй только те, что про ИМЕННО предмет вопроса (тот же материал/элемент/"
    "процесс/величина). КРИТИЧНО: не приписывай предмету вопроса данные про другое "
    "вещество — например, не выдавай данные про кобальт за данные про МПГ, про "
    "железо за иридий и т.п. Частичный ответ по релевантным фрагментам лучше "
    "отказа. Верни РОВНО «НЕТ ДАННЫХ» только если НИ ОДИН фрагмент не относится к "
    "предмету вопроса.\n"
    "3) В конце в скобках укажи документ(ы)-источник(и) по НАЗВАНИЮ. Не ссылайся "
    "на номера фрагментов ([1], «запрос 3» и т.п.) — только названия документов.\n"
    "4) Не пиши преамбул вроде «на основании фрагментов».\n"
    "5) Ответь ОДНИМ абзацем — без переносов строк, маркированных списков и "
    "заголовков (перечисления давай через запятую/точку с запятой в строку)."
)


def _fragments(claims: list[dict], max_frag: int) -> list[str]:
    out: list[str] = []
    for i, c in enumerate(claims[:max_frag], 1):
        text = (c.get("text") or "").strip()
        if not text:
            continue
        cite = ""
        for s in c.get("citations") or []:
            if s:
                cite = s
                break
        # источник в цитате имеет вид «Документ: сниппет» — берём имя документа
        doc = cite.split(":", 1)[0].strip() if cite else ""
        head = f"[{i}] {text[:500]}"
        if doc:
            head += f"  (документ: {doc[:70]})"
        out.append(head)
    return out


def synthesize(question: str, claims: list[dict], max_frag: int = 8) -> str | None:
    """Вопрос + пассажи → синтезированный ответ, `NO_DATA`, либо None при сбое."""
    frags = _fragments(claims, max_frag)
    if not frags:
        return None
    user = (f"ВОПРОС: {question}\n\nФРАГМЕНТЫ:\n" + "\n".join(frags)
            + "\n\nОтвет:")
    try:
        client = intent_llm._ensure_client()
        model = intent_llm._model
        kwargs: dict = dict(
            model=model, temperature=0, max_tokens=500,
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}])
        if model and "Gpt-oss" in model:
            kwargs["reasoning_effort"] = "low"
        r = client.chat.completions.create(**kwargs)
        ans = (r.choices[0].message.content or "").strip()
    except Exception:
        return None
    if not ans:
        return None
    # нормализуем сигнал отсутствия данных
    head = ans.upper().replace("«", "").replace("»", "").strip()
    if head.startswith(NO_DATA) or head.startswith("NO DATA"):
        return NO_DATA
    return ans
