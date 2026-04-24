"""Generic fallback strategy — no platform-specific overrides."""
from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy


@register_strategy
class GenericStrategy(BasePlatformStrategy):
    name = "generic"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return False
