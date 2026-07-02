"""Refresh official media for completed matches; suitable for a scheduled job."""

from app.api.dependencies import get_media_repository, get_youtube_highlight_client
from app.core.config import get_settings
from app.services.media import MatchMediaResolverService


def main() -> None:
    settings = get_settings()
    repository = get_media_repository()
    service = MatchMediaResolverService(repository, get_youtube_highlight_client())
    matches = repository.ended_matches(settings.media_refresh_batch_size)
    for match in matches:
        service.resolve(match)
    print(f"Checked {len(matches)} completed matches")


if __name__ == "__main__":
    main()
