from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.aggregators.lastfm import LastFmAggregator
from src.skills.music.aggregators.deezer import DeezerAggregator
from src.skills.music.aggregators.beatport import BeatportAggregator
from src.skills.music.aggregators.charts import ChartsAggregator
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.ytmusic import YouTubeMusicAggregator
from src.skills.music.aggregators.manager import MusicAggregatorManager

__all__ = [
    "BaseMusicAggregator",
    "LastFmAggregator",
    "DeezerAggregator",
    "BeatportAggregator",
    "ChartsAggregator",
    "FreqBlogAggregator",
    "YouTubeMusicAggregator",
    "MusicAggregatorManager",
]
