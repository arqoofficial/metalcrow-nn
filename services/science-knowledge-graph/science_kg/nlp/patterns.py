"""Domain-specific EntityRuler patterns for materials science (RU + EN)."""

from science_kg.nlp.term_dictionary_patterns import load_mapped_patterns

# Units of measurement — explicitly excluded from MATERIAL patterns
_UNITS = {
    "mpa",
    "gpa",
    "kpa",
    "pa",
    "hv",
    "hrc",
    "hrb",
    "hb",
    "hv5",
    "hv10",
    "hv0",
    "lpbf",
    "slm",
    "ebm",  # process abbreviations, not materials
    "sem",
    "tem",
    "xrd",  # equipment abbreviations
    "ppm",
    "wt",
    "at",
}

MATERIAL_PATTERNS = [
    # ── Titanium alloys (explicit dictionary) ────────────────────────────────
    {"label": "MATERIAL", "pattern": "Ti-6Al-4V"},
    {"label": "MATERIAL", "pattern": "Ti6Al4V"},
    {"label": "MATERIAL", "pattern": "Ti-24Nb-4Zr-8Sn"},
    {"label": "MATERIAL", "pattern": "Ti2448"},
    {"label": "MATERIAL", "pattern": "Ti555211"},
    {"label": "MATERIAL", "pattern": "Ti-3Al-5Mo-4Cr-2Zr-1Fe"},
    {"label": "MATERIAL", "pattern": "Ti-35421"},
    {"label": "MATERIAL", "pattern": "Ti-B19"},
    {"label": "MATERIAL", "pattern": "ВТ6"},
    {"label": "MATERIAL", "pattern": "ВТ20"},
    {"label": "MATERIAL", "pattern": "ВТ22"},
    {"label": "MATERIAL", "pattern": "ОТ4"},
    {"label": "MATERIAL", "pattern": "ВТ14"},
    # ── Aluminium alloys ─────────────────────────────────────────────────────
    {"label": "MATERIAL", "pattern": "Д16"},
    {"label": "MATERIAL", "pattern": "АМг6"},
    {"label": "MATERIAL", "pattern": "В95"},
    {"label": "MATERIAL", "pattern": "1420"},
    # ── Nickel superalloys ───────────────────────────────────────────────────
    {"label": "MATERIAL", "pattern": "ЖС6У"},
    {"label": "MATERIAL", "pattern": "ЖС32"},
    {"label": "MATERIAL", "pattern": "ЭП741НП"},
    {
        "label": "MATERIAL",
        "pattern": [{"TEXT": "Inconel"}, {"TEXT": {"REGEX": r"^\d+"}}],
    },
    # ── Steel ────────────────────────────────────────────────────────────────
    {"label": "MATERIAL", "pattern": "12Х18Н10Т"},
    {"label": "MATERIAL", "pattern": "ШХ15"},
    {"label": "MATERIAL", "pattern": "Р6М5"},
    # ── Token-level regex patterns ───────────────────────────────────────────
    # Chemical formulas: Al2O3, TiN, ZrO2, SiC
    # Requires lowercase after uppercase — excludes MPa (M+Pa→no lowercase after M)
    # and pure abbreviations like LPBF, SEM.
    {
        "label": "MATERIAL",
        "pattern": [{"TEXT": {"REGEX": r"^[A-Z][a-z]\d*([A-Z][a-z]\d*)+$"}}],
    },
    # Binary compounds with single-letter elements: TiN, TiC, VC, WC, BN
    {
        "label": "MATERIAL",
        "pattern": [{"TEXT": {"REGEX": r"^[A-Z][a-z][BCNOF]\d*$"}}],
    },
    # Ti-alloys with hyphen notation: Ti-6Al-4V style (not already in dict above)
    {
        "label": "MATERIAL",
        "pattern": [{"TEXT": {"REGEX": r"^Ti-\d+[A-Z][a-z].*$"}}],
    },
    # Ti-alloys numeric: Ti555211, Ti2448, Ti64
    {
        "label": "MATERIAL",
        "pattern": [{"TEXT": {"REGEX": r"^Ti\d{2,}$"}}],
    },
]

PROCESS_PATTERNS = [
    # Temperature: "850°C", "1200°C", "850 К"
    {
        "label": "PROCESS",
        "pattern": [
            {"LIKE_NUM": True},
            {"TEXT": {"IN": ["°C", "°F", "К", "K"]}},
        ],
    },
    # Temperature with space: "850 ° C"
    {
        "label": "PROCESS",
        "pattern": [
            {"LIKE_NUM": True},
            {"TEXT": "°"},
            {"TEXT": {"IN": ["C", "F"]}},
        ],
    },
    # PDF OCR artefact: "950 oC", "800 oF" (lowercase 'o' instead of degree sign)
    {
        "label": "PROCESS",
        "pattern": [
            {"LIKE_NUM": True},
            {"TEXT": {"IN": ["oC", "oF"]}},
        ],
    },
    # Duration: "2 часа", "30 минут", "4 hours", "30 min"
    {
        "label": "PROCESS",
        "pattern": [
            {"LIKE_NUM": True},
            {
                "LOWER": {
                    "IN": [
                        "час",
                        "часа",
                        "часов",
                        "ч",
                        "мин",
                        "минут",
                        "минуты",
                        "с",
                        "сек",
                        "hour",
                        "hours",
                        "h",
                        "min",
                        "minutes",
                        "second",
                        "seconds",
                    ]
                }
            },
        ],
    },
    # Pressure: "0.1 МПа", "100 кПа", "1 атм"
    {
        "label": "PROCESS",
        "pattern": [
            {"LIKE_NUM": True},
            {"LOWER": {"IN": ["мпа", "кпа", "па", "атм", "bar", "бар", "torr"]}},
        ],
    },
    # Atmosphere: vacuum, inert gas
    {
        "label": "PROCESS",
        "pattern": [
            {
                "LOWER": {
                    "IN": [
                        "вакуум",
                        "аргон",
                        "азот",
                        "воздух",
                        "vacuum",
                        "argon",
                        "nitrogen",
                        "air",
                    ]
                }
            }
        ],
    },
    # "in vacuum", "in argon"
    {
        "label": "PROCESS",
        "pattern": [
            {"LOWER": "in"},
            {"LOWER": {"IN": ["vacuum", "argon", "nitrogen", "air", "atmosphere"]}},
        ],
    },
    # Heating/cooling rate: "10 °C/min"
    {
        "label": "PROCESS",
        "pattern": [
            {"LIKE_NUM": True},
            {"TEXT": {"REGEX": r"^°[CF]/(?:min|с|ч|h|s)$"}},
        ],
    },
    # Cooling method as multi-token phrase (avoids single "furnace" → PROCESS clash with EQUIPMENT)
    {
        "label": "PROCESS",
        "pattern": [
            {"LOWER": {"IN": ["furnace", "water", "air", "slow"]}},
            {"LOWER": {"IN": ["cool", "cooling", "quench", "quenching"]}},
        ],
    },
    {
        "label": "PROCESS",
        "pattern": [
            {
                "LOWER": {
                    "IN": [
                        "quenching",
                        "water-quenching",
                        "air-cooling",
                        "закалка",
                        "охлаждение",
                    ]
                }
            }
        ],
    },
]

EXPERIMENT_PATTERNS = [
    # "образец 1", "образец №3", "образец A"
    {
        "label": "EXPERIMENT",
        "pattern": [
            {"LOWER": {"IN": ["образец", "образцы", "серия", "партия"]}},
            {"TEXT": "№", "OP": "?"},
            {"TEXT": {"REGEX": r"^[A-Za-zА-Яа-я0-9]+$"}},
        ],
    },
    # "sample 1", "sample A", "experiment 3", "series 2"
    {
        "label": "EXPERIMENT",
        "pattern": [
            {"LOWER": {"IN": ["sample", "experiment", "series", "batch", "run"]}},
            {"TEXT": {"REGEX": r"^[A-Za-z0-9]+$"}},
        ],
    },
]

FACILITY_PATTERNS = [
    # Norilsk Nickel domain — explicit dictionary (NER's ORG label is a
    # complementary source, see nlp/extractor.py::_LABEL_REMAP; this list
    # catches names/abbreviations general-purpose NER tends to miss).
    {"label": "FACILITY", "pattern": "КГМК"},
    {"label": "FACILITY", "pattern": "Кольская ГМК"},
    {"label": "FACILITY", "pattern": "Надеждинский металлургический завод"},
    {"label": "FACILITY", "pattern": "Надеждинский МЗ"},
    {"label": "FACILITY", "pattern": "Норильский никель"},
    {"label": "FACILITY", "pattern": "Норникель"},
    {"label": "FACILITY", "pattern": "МИСиС"},
    {"label": "FACILITY", "pattern": "ИМЕТ РАН"},
]

PROPERTY_PATTERNS = [
    # Russian mechanical properties
    {
        "label": "PROPERTY",
        "pattern": [
            {
                "LOWER": {
                    "IN": [
                        "прочность",
                        "твёрдость",
                        "твердость",
                        "пластичность",
                        "вязкость",
                        "жёсткость",
                        "жесткость",
                        "усталость",
                        "износостойкость",
                        "коррозионная стойкость",
                        "жаропрочность",
                        "жаростойкость",
                        "термостойкость",
                    ]
                }
            }
        ],
    },
    # Russian compound: "предел прочности / текучести / выносливости"
    {
        "label": "PROPERTY",
        "pattern": [
            {"LOWER": "предел"},
            {"LOWER": {"IN": ["прочности", "текучести", "выносливости", "усталости"]}},
        ],
    },
    # English mechanical properties — LEMMA matching handles plurals (strengths→strength)
    {
        "label": "PROPERTY",
        "pattern": [
            {
                "LEMMA": {
                    "IN": [
                        "hardness",
                        "strength",
                        "ductility",
                        "toughness",
                        "stiffness",
                        "plasticity",
                        "elongation",
                        "fatigue",
                        "wear",
                        "corrosion",
                        "creep",
                        "modulus",
                        "property",
                    ]
                }
            }
        ],
    },
    # English compound: "tensile strength", "yield strength", "fracture toughness"
    {
        "label": "PROPERTY",
        "pattern": [
            {
                "LOWER": {
                    "IN": [
                        "tensile",
                        "yield",
                        "compressive",
                        "flexural",
                        "shear",
                        "fracture",
                        "ultimate",
                    ]
                }
            },
            {
                "LEMMA": {
                    "IN": ["strength", "toughness", "modulus", "stress", "property"]
                }
            },
        ],
    },
    # Microstructure — LEMMA handles microstructures→microstructure
    {
        "label": "PROPERTY",
        "pattern": [
            {
                "LEMMA": {
                    "IN": [
                        "microstructure",
                        "grain",
                        "texture",
                        "phase",
                        "микроструктура",
                        "зернистость",
                        "текстура",
                    ]
                }
            }
        ],
    },
]

EQUIPMENT_PATTERNS = [
    # Russian equipment
    {
        "label": "EQUIPMENT",
        "pattern": [
            {
                "LOWER": {
                    "IN": ["печь", "печи", "печью", "пресс", "установка", "установки"]
                }
            }
        ],
    },
    {
        "label": "EQUIPMENT",
        "pattern": [
            {"LOWER": {"IN": ["дифрактометр", "микроскоп", "спектрометр", "твердомер"]}}
        ],
    },
    # Abbreviations
    {
        "label": "EQUIPMENT",
        "pattern": [
            {"TEXT": {"IN": ["СЭМ", "ПЭМ", "РФА", "SEM", "TEM", "XRD", "EBSD"]}}
        ],
    },
    # English equipment
    {
        "label": "EQUIPMENT",
        "pattern": [
            {
                "LOWER": {
                    "IN": [
                        "furnace",
                        "press",
                        "diffractometer",
                        "microscope",
                        "spectrometer",
                    ]
                }
            }
        ],
    },
    # LPBF, SLM, EBM — manufacturing processes used as "equipment" context
    {
        "label": "EQUIPMENT",
        "pattern": [{"TEXT": {"IN": ["LPBF", "SLM", "EBM", "PVD", "CVD"]}}],
    },
]

# Numeric values (was its own VALUE type) — folded into PROPERTY: a number
# like "980 МПа" is the quantified form of a property, same entity type as
# the property name itself ("предел прочности") post-SPEC_V5 rename.
VALUE_PATTERNS = [
    # "980 МПа", "42 HRC", "290 HV5", "1404 MPa"
    {
        "label": "PROPERTY",
        "pattern": [
            {"LIKE_NUM": True},
            {
                "LOWER": {
                    "IN": [
                        "мпа",
                        "кпа",
                        "гпа",
                        "мн/м²",
                        "mpa",
                        "gpa",
                        "kpa",
                        "hrc",
                        "hrb",
                        "hb",
                        "hv",
                        "hv5",
                        "hv10",
                        "hv0.1",
                        "мкм",
                        "нм",
                        "мм",
                        "μm",
                        "nm",
                        "mm",
                    ]
                }
            },
        ],
    },
    # Percentage: "11%", "54 %"
    {"label": "PROPERTY", "pattern": [{"LIKE_NUM": True}, {"TEXT": "%"}]},
    # GPa Young's modulus style: "50 GPa"
    {
        "label": "PROPERTY",
        "pattern": [{"LIKE_NUM": True}, {"TEXT": {"IN": ["GPa", "MPa", "KPa"]}}],
    },
]

HAND_WRITTEN_PATTERNS = (
    MATERIAL_PATTERNS
    + PROCESS_PATTERNS
    + PROPERTY_PATTERNS
    + EQUIPMENT_PATTERNS
    + VALUE_PATTERNS
    + EXPERIMENT_PATTERNS
    + FACILITY_PATTERNS
)

# Hand-written patterns first: this project's own 73-test suite is tuned against
# them. term_dictionary's ~360 mapped patterns (of 924 total — the rest are
# labels this service doesn't model, see term_dictionary_patterns.LABEL_MAP)
# are appended, not merged with priority logic — if a label conflict ever shows
# up on the same span, a test will catch it and we'll fix it then, not guess now.
ALL_PATTERNS = HAND_WRITTEN_PATTERNS + load_mapped_patterns()
