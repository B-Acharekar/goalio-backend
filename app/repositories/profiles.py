from typing import Protocol

from fastapi import HTTPException, status
from firebase_admin import auth, firestore
from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore_v1 import Client

from app.schemas.profile import ProfileUpsert, UserProfile


class ProfileRepository(Protocol):
    def get(self, uid: str) -> UserProfile | None: ...
    def upsert(self, uid: str, profile: ProfileUpsert) -> UserProfile: ...
    def is_username_available(self, username: str, uid: str) -> bool: ...
    def profile_login(self, name: str, username: str) -> str: ...
    def profile_matches(self, name: str, username: str) -> bool: ...


class FirestoreProfileRepository:
    def __init__(self, client: Client):
        self.client = client

    def get(self, uid: str) -> UserProfile | None:
        try:
            snapshot = self.client.collection("users").document(uid).get()
        except GoogleAPICallError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Cloud Firestore is unavailable. Enable the Firestore API and create the default database for this Firebase project.",
            ) from exc
        if not snapshot.exists:
            return None
        return UserProfile(userId=uid, **snapshot.to_dict())

    def is_username_available(self, username: str, uid: str) -> bool:
        try:
            snapshot = self.client.collection("usernames").document(username).get()
        except GoogleAPICallError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Cloud Firestore is unavailable. Enable the Firestore API and create the default database for this Firebase project.",
            ) from exc
        return not snapshot.exists or snapshot.to_dict().get("userId") == uid

    def profile_login(self, name: str, username: str) -> str:
        uid = self._matching_profile_uid(name, username)
        if uid is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Full name or username did not match")
        token = auth.create_custom_token(uid)
        return token.decode("utf-8") if isinstance(token, bytes) else str(token)

    def profile_matches(self, name: str, username: str) -> bool:
        return self._matching_profile_uid(name, username) is not None

    def _matching_profile_uid(self, name: str, username: str) -> str | None:
        normalized_username = username.strip().lower()
        normalized_name = " ".join(name.strip().split()).casefold()
        username_snapshot = self.client.collection("usernames").document(normalized_username).get()
        if not username_snapshot.exists:
            return None
        uid = username_snapshot.to_dict().get("userId")
        user_snapshot = self.client.collection("users").document(uid).get() if uid else None
        stored_name = " ".join((user_snapshot.to_dict().get("name") if user_snapshot and user_snapshot.exists else "").strip().split()).casefold()
        if not uid or stored_name != normalized_name:
            return None
        return str(uid)

    def _resolve_favorites(self, collection_name: str, ids: list[str]) -> list[str]:
        if not ids:
            return []
        references = [
            self.client.collection(collection_name).document(item_id)
            for item_id in ids
        ]
        try:
            snapshots = list(self.client.get_all(references))
        except GoogleAPICallError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Cloud Firestore is unavailable while validating favorites.",
            ) from exc
        records = {
            snapshot.id: snapshot.to_dict()
            for snapshot in snapshots
            if snapshot.exists and snapshot.to_dict().get("active") is True
        }
        missing = [item_id for item_id in ids if item_id not in records]
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown or inactive {collection_name}: {', '.join(missing)}",
            )
        return [records[item_id]["name"] for item_id in ids]

    def upsert(self, uid: str, profile: ProfileUpsert) -> UserProfile:
        favorite_teams = self._resolve_favorites("teams", profile.favoriteTeamIds)
        favorite_players = self._resolve_favorites("players", profile.favoritePlayerIds)
        user_ref = self.client.collection("users").document(uid)
        username_ref = self.client.collection("usernames").document(profile.username)
        transaction = self.client.transaction()

        @firestore.transactional
        def save_profile(tx):
            current = user_ref.get(transaction=tx)
            reserved = username_ref.get(transaction=tx)
            if reserved.exists and reserved.to_dict().get("userId") != uid:
                raise HTTPException(status.HTTP_409_CONFLICT, "Username is already taken")

            old_username = current.to_dict().get("username") if current.exists else None
            if old_username and old_username != profile.username:
                tx.delete(self.client.collection("usernames").document(old_username))

            payload = profile.model_dump()
            payload.update(
                {
                    "favoriteTeams": favorite_teams,
                    "favoritePlayers": favorite_players,
                    "profileCompleted": True,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }
            )
            if not current.exists:
                payload["createdAt"] = firestore.SERVER_TIMESTAMP
            tx.set(user_ref, payload, merge=True)
            tx.set(username_ref, {"userId": uid, "updatedAt": firestore.SERVER_TIMESTAMP})

        try:
            save_profile(transaction)
        except GoogleAPICallError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Cloud Firestore is unavailable. Enable the Firestore API and create the default database for this Firebase project.",
            ) from exc
        return self.get(uid)  # type: ignore[return-value]
