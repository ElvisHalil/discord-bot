"""
Static catalogs, regional edition registries, and topic mappings for the News domain skill.
"""

from __future__ import annotations

from typing import Dict

from src.skills.news.enums import NewsTopic
from src.skills.news.models import NewsEdition

# Countries with an English Google News edition: region alias -> NewsEdition(hl, gl)
NEWS_EDITIONS: Dict[str, NewsEdition] = {
    "us": NewsEdition("en-US", "US"),
    "usa": NewsEdition("en-US", "US"),
    "united states": NewsEdition("en-US", "US"),
    "america": NewsEdition("en-US", "US"),
    "canada": NewsEdition("en-CA", "CA"),
    "ca": NewsEdition("en-CA", "CA"),
    "uk": NewsEdition("en-GB", "GB"),
    "united kingdom": NewsEdition("en-GB", "GB"),
    "britain": NewsEdition("en-GB", "GB"),
    "great britain": NewsEdition("en-GB", "GB"),
    "england": NewsEdition("en-GB", "GB"),
    "australia": NewsEdition("en-AU", "AU"),
    "india": NewsEdition("en-IN", "IN"),
    "ireland": NewsEdition("en-IE", "IE"),
    "new zealand": NewsEdition("en-NZ", "NZ"),
    "south africa": NewsEdition("en-ZA", "ZA"),
    "singapore": NewsEdition("en-SG", "SG"),
}

GLOBAL_REGIONS: frozenset[str] = frozenset({"world", "global", "international", "worldwide"})

# Google News section topics
NEWS_TOPICS_MAP: Dict[str, NewsTopic] = {
    "world": NewsTopic.WORLD,
    "international": NewsTopic.WORLD,
    "nation": NewsTopic.NATION,
    "national": NewsTopic.NATION,
    "business": NewsTopic.BUSINESS,
    "economy": NewsTopic.BUSINESS,
    "finance": NewsTopic.BUSINESS,
    "technology": NewsTopic.TECHNOLOGY,
    "tech": NewsTopic.TECHNOLOGY,
    "entertainment": NewsTopic.ENTERTAINMENT,
    "sports": NewsTopic.SPORTS,
    "sport": NewsTopic.SPORTS,
    "science": NewsTopic.SCIENCE,
    "health": NewsTopic.HEALTH,
}

# Topic values that just mean "the main headlines" when a region is given.
GENERIC_TOPICS: frozenset[str] = frozenset({
    "world", "international", "global", "news", "general", "top", "top stories", "top news",
    "headlines", "top headlines", "latest", "breaking", "all", "nation", "national", "politics",
})

