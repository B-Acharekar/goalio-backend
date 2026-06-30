import json
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

from app.core.config import get_settings


def initialize_firebase() -> firebase_admin.App:
    if firebase_admin._apps:
        return firebase_admin.get_app()

    settings = get_settings()
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    credential_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    local_key = next(Path(__file__).parents[2].glob("*-firebase-adminsdk-*.json"), None)
    if credential_json:
        credential = credentials.Certificate(json.loads(credential_json))
    elif credential_path:
        credential = credentials.Certificate(credential_path)
    elif local_key and local_key.exists():
        credential = credentials.Certificate(str(local_key))
    else:
        credential = credentials.ApplicationDefault()

    return firebase_admin.initialize_app(
        credential,
        {"projectId": settings.firebase_project_id},
    )


def get_firestore_client():
    initialize_firebase()
    return firestore.client()
