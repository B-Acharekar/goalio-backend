from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileUpsert(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=3, max_length=20)
    favoriteTeamIds: list[str] = Field(default_factory=list, max_length=6)
    favoritePlayerIds: list[str] = Field(default_factory=list, max_length=6)
    onboardingCompleted: bool = True

    @field_validator("name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        parts = normalized.split(" ")
        if len(parts) < 2:
            raise ValueError("Enter your full name with at least first and last name")
        for part in parts:
            letters = part.replace("-", "").replace("'", "")
            if len(letters) < 2 or not letters.isalpha():
                raise ValueError("Each name must contain at least two letters")
        return normalized

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,19}", normalized):
            raise ValueError("Username must start with a letter and use only lowercase letters, numbers, or underscores")
        if normalized.endswith("_") or "__" in normalized:
            raise ValueError("Username cannot end with an underscore or contain consecutive underscores")
        if normalized in {"admin", "administrator", "goalio", "support", "moderator", "root", "system"}:
            raise ValueError("This username is reserved")
        return normalized

    @field_validator("favoriteTeamIds", "favoritePlayerIds")
    @classmethod
    def clean_favorite_ids(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in cleaned:
                cleaned.append(item[:120])
        return cleaned


class UserProfile(ProfileUpsert):
    userId: str
    favoriteTeams: list[str] = Field(default_factory=list)
    favoritePlayers: list[str] = Field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    profileCompleted: bool = True

    model_config = ConfigDict(from_attributes=True)


class PersonalizedHome(BaseModel):
    profile: UserProfile
    greeting: str


class UsernameAvailability(BaseModel):
    username: str
    available: bool


class ProfileLoginRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=3, max_length=20)


class ProfileLoginResponse(BaseModel):
    customToken: str


class ProfileIdentityMatch(BaseModel):
    matched: bool
