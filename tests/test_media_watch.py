from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.matches import MatchDetail, MatchTeam
from app.schemas.media import StoredMatchMedia, WatchProvider, WatchProviderConfig
from app.services.media import MatchMediaResolverService, YouTubeQuotaError, validate_official_video
from app.services.watch import FIFA_FALLBACK, WatchProviderResolverService, environment_watch_config, is_legal_provider, normalize_country


NOW = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)


def match(status="Final", kickoff="2026-07-01T20:00:00Z"):
    return MatchDetail(matchId="760493", league="fifa.world", status=status, kickoff=kickoff,
                       homeTeam=MatchTeam(id="459", name="Belgium", score=2),
                       awayTeam=MatchTeam(id="654", name="Senegal", score=1))


def video(channel="fifa", published="2026-07-01T23:00:00Z", title="Belgium v Senegal Highlights"):
    return {"id": {"videoId": "official123"}, "snippet": {"channelId": channel, "title": title, "publishedAt": published, "thumbnails": {"high": {"url": "https://img.youtube.com/x.jpg"}}}}


class MediaRepo:
    def __init__(self, value=None): self.value, self.writes = value, []
    def get(self, match_id): return self.value
    def put(self, value): self.value = value; self.writes.append(value)
    def ended_matches(self, limit): return [match()][:limit]


class Youtube:
    def __init__(self, result=None, error=None): self.result, self.error, self.calls = result, error, 0
    def search(self, value):
        self.calls += 1
        if self.error: raise self.error
        return self.result


def test_official_fifa_video_accepted():
    assert validate_official_video(video(), match(), {"fifa"}) == (True, "accepted")


def test_fan_video_rejected():
    accepted, reason = validate_official_video(video(channel="fan"), match(), {"fifa"})
    assert not accepted and reason == "channel_not_allowed"


def test_old_video_rejected():
    accepted, reason = validate_official_video(video(published="2026-06-01T10:00:00Z"), match(), {"fifa"})
    assert not accepted and reason == "published_before_kickoff"


def test_cached_available_highlight_returned_without_search():
    cached = StoredMatchMedia(matchId="760493", highlightStatus="available", highlightProvider="FIFA YouTube", highlightUrl="https://youtube.com/watch?v=x", source="youtube_api", lastCheckedAt=NOW - timedelta(hours=1))
    repo, youtube = MediaRepo(cached), Youtube()
    result = MatchMediaResolverService(repo, youtube, now=lambda: NOW).resolve(match())
    assert result.source == "cache" and youtube.calls == 0


def test_pending_fallback_stored_when_highlight_missing():
    repo = MediaRepo()
    result = MatchMediaResolverService(repo, Youtube(), now=lambda: datetime(2026, 7, 1, 23, tzinfo=timezone.utc)).resolve(match())
    assert result.highlightStatus == "pending" and repo.writes


def test_youtube_quota_failure_returns_cached_or_safe_pending():
    result = MatchMediaResolverService(MediaRepo(), Youtube(error=YouTubeQuotaError("quota")), now=lambda: NOW).resolve(match())
    assert result.highlightStatus == "pending" and result.highlightUrl is None


class WatchRepo:
    def __init__(self, config=None): self.config, self.cached = config, None
    def get_config(self, competition, season): return self.config
    def cache_match(self, match_id, config, source): self.cached = (match_id, source)
    def upsert(self, value): raise NotImplementedError


def watch_config():
    provider = WatchProvider(name="Official Sports", type="live", url="https://official.example/watch", isFree=None)
    return WatchProviderConfig(competition="FIFA World Cup", season="2026", regions={"IN": [provider]}, fallback=FIFA_FALLBACK, updatedAt=NOW)


def test_watch_provider_found_for_normalized_country():
    result = WatchProviderResolverService(WatchRepo(watch_config())).resolve(match(), "in")
    assert result.country == "IN" and result.status == "available" and len(result.providers) == 1


def test_watch_provider_missing_returns_official_fallback():
    result = WatchProviderResolverService(WatchRepo(watch_config())).resolve(match(), "CA")
    assert result.status == "not_available" and not result.providers and result.fallback.name == "FIFA Match Centre"


def test_empty_country_uses_global_fallback():
    assert normalize_country("") == "GLOBAL"
    result = WatchProviderResolverService(WatchRepo()).resolve(match(), "")
    assert result.country == "GLOBAL" and result.status == "not_available"


def test_invalid_or_unofficial_provider_rejected():
    provider = WatchProvider(name="Free Stream VIPBox", type="live", url="https://vipbox.example")
    assert not is_legal_provider(provider)
    with pytest.raises(ValueError):
        from app.services.watch import validate_provider
        validate_provider(provider)


def test_environment_watch_config_and_http_rejection():
    raw = '{"watchProviders":{"FIFA World Cup:2026":{"regions":{"IN":[{"name":"Official","type":"live","url":"https://official.example"}]}}}}'
    config = environment_watch_config(raw, "FIFA World Cup", "2026")
    assert config and config.regions["IN"][0].name == "Official"
    direct = environment_watch_config('{"watchProviders":{"IN":[{"name":"Official","type":"live","url":"https://official.example"}]}}', "FIFA World Cup", "2026")
    assert direct and direct.regions["IN"][0].name == "Official"
    assert not is_legal_provider(WatchProvider(name="Official", type="live", url="http://official.example"))
