from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import CurrentUser, get_current_user, get_profile_repository
from app.repositories.profiles import ProfileRepository
from app.schemas.profile import PersonalizedHome, ProfileIdentityMatch, ProfileLoginRequest, ProfileLoginResponse, ProfileUpsert, UserProfile, UsernameAvailability


router = APIRouter(
    tags=["users"],
    responses={
        401: {"description": "Missing, invalid, expired, or revoked Firebase ID token"},
        503: {"description": "Cloud Firestore API/database is unavailable"},
        422: {"description": "Invalid request data"},
    },
)


@router.post("/auth/profile-login", response_model=ProfileLoginResponse)
def profile_login(payload: ProfileLoginRequest, repository: ProfileRepository = Depends(get_profile_repository)) -> ProfileLoginResponse:
    return ProfileLoginResponse(customToken=repository.profile_login(payload.name, payload.username))


@router.post("/users/profile/identity-match", response_model=ProfileIdentityMatch)
def profile_identity_match(
    payload: ProfileLoginRequest,
    _: CurrentUser = Depends(get_current_user),
    repository: ProfileRepository = Depends(get_profile_repository),
) -> ProfileIdentityMatch:
    return ProfileIdentityMatch(matched=repository.profile_matches(payload.name, payload.username))


@router.post("/users/profile", response_model=UserProfile)
def save_profile(
    payload: ProfileUpsert,
    user: CurrentUser = Depends(get_current_user),
    repository: ProfileRepository = Depends(get_profile_repository),
) -> UserProfile:
    return repository.upsert(user.uid, payload)


@router.get("/users/username/availability", response_model=UsernameAvailability)
def username_availability(
    username: str,
    user: CurrentUser = Depends(get_current_user),
    repository: ProfileRepository = Depends(get_profile_repository),
) -> UsernameAvailability:
    normalized = ProfileUpsert(
        name="Valid Person",
        username=username,
    ).username
    return UsernameAvailability(
        username=normalized,
        available=repository.is_username_available(normalized, user.uid),
    )


@router.get("/users/profile", response_model=UserProfile)
def get_profile(
    user: CurrentUser = Depends(get_current_user),
    repository: ProfileRepository = Depends(get_profile_repository),
) -> UserProfile:
    profile = repository.get(user.uid)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return profile


@router.get("/home", response_model=PersonalizedHome)
def get_home(
    user: CurrentUser = Depends(get_current_user),
    repository: ProfileRepository = Depends(get_profile_repository),
) -> PersonalizedHome:
    profile = repository.get(user.uid)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Complete your profile first")
    first_name = profile.name.split()[0]
    return PersonalizedHome(profile=profile, greeting=f"Welcome back, {first_name}")
