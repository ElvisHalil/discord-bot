from typing import Optional

from src.skills.news.aggregators.base import BaseNewsAggregator


class GoogleNewsAggregator(BaseNewsAggregator):
    """Google News RSS aggregator adapter."""

    def get_headlines(self, region: Optional[str] = None, topic: Optional[str] = None) -> str:
        from src.skills.news.news import skill_news_headlines

        return skill_news_headlines(region=region, topic=topic)
