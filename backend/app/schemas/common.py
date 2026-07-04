from enum import StrEnum


# Пороги bucket из dictionaries/regime_buckets.yaml (SPEC_V3 §4/§5.4): low <400°C,
# medium 400-800°C, high >800°C. Общий enum для GapCell (chat) и heatmap (analytics).
class RegimeBucket(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
