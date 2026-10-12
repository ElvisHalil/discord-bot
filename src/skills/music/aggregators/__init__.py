from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.aggregators.deezer import DeezerAggregator
from src.skills.music.aggregators.beatport import BeatportAggregator
from src.skills.music.aggregators.charts import ChartsAggregator
from src.skills.music.aggregators.manager import MusicAggregatorManager

__all__ = [
    "BaseMusicAggregator",
    "DeezerAggregator",
    "BeatportAggregator",
    "ChartsAggregator",
    "MusicAggregatorManager",
]
