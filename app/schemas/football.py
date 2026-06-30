from pydantic import BaseModel


class TeamResult(BaseModel):
    id: str
    name: str
    shortName: str
    imageUrl: str | None = None


class PlayerResult(BaseModel):
    id: str
    name: str
    team: str
    imageUrl: str | None = None


class TeamPage(BaseModel):
    items: list[TeamResult]
    nextCursor: str | None = None


class PlayerPage(BaseModel):
    items: list[PlayerResult]
    nextCursor: str | None = None
