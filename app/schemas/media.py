from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class HighlightView(BaseModel):
    status: Literal["pending", "available", "not_found"]
    provider: str | None = None
    url: str | None = None
    embedUrl: str | None = None
    thumbnailUrl: str | None = None
    publishedAt: datetime | None = None


class OfficialMediaView(BaseModel):
    highlightsPageUrl: str | None = None
    matchUrl: str | None = None


class MatchMediaResponse(BaseModel):
    matchId: str
    highlight: HighlightView
    official: OfficialMediaView
    source: Literal["cache", "youtube_api", "fifa_fallback", "manual"]
    lastCheckedAt: datetime


class StoredMatchMedia(BaseModel):
    matchId: str
    highlightStatus: Literal["pending", "available", "not_found"]
    highlightProvider: str | None = None
    highlightUrl: str | None = None
    highlightEmbedUrl: str | None = None
    youtubeVideoId: str | None = None
    thumbnailUrl: str | None = None
    officialHighlightsPageUrl: str | None = None
    officialMatchUrl: str | None = None
    source: Literal["cache", "youtube_api", "fifa_fallback", "manual"]
    lastCheckedAt: datetime
    publishedAt: datetime | None = None

    def response(self, cached: bool = False) -> MatchMediaResponse:
        return MatchMediaResponse(matchId=self.matchId, highlight=HighlightView(status=self.highlightStatus, provider=self.highlightProvider, url=self.highlightUrl, embedUrl=self.highlightEmbedUrl, thumbnailUrl=self.thumbnailUrl, publishedAt=self.publishedAt), official=OfficialMediaView(highlightsPageUrl=self.officialHighlightsPageUrl, matchUrl=self.officialMatchUrl), source="cache" if cached else self.source, lastCheckedAt=self.lastCheckedAt)


WatchType = Literal["live", "highlights", "official_page", "broadcaster_app"]


class WatchProvider(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: WatchType
    url: HttpUrl
    appPackage: str | None = Field(default=None, max_length=200)
    isFree: bool | None = None
    note: str | None = Field(default=None, max_length=300)


class WatchProviderConfig(BaseModel):
    competition: str
    season: str
    regions: dict[str, list[WatchProvider]] = Field(default_factory=dict)
    fallback: WatchProvider
    updatedAt: datetime


class MatchWatchResponse(BaseModel):
    matchId: str
    country: str
    status: Literal["available", "not_available"]
    providers: list[WatchProvider]
    fallback: WatchProvider
    message: str | None = None
    disclaimer: str = "Streaming availability depends on your region and broadcaster rights."
