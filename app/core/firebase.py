import json
import os
import threading
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

from app.core.config import get_settings


_firebase_lock = threading.Lock()

def _load_credential():
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    credential_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    
    # Robustly resolve the project root directory
    root_dir = Path(__file__).resolve().parent.parent.parent
    local_key = next(root_dir.glob("*-firebase-adminsdk-*.json"), None)
    
    if credential_path:
        return credentials.Certificate(credential_path)
    if credential_json:
        return credentials.Certificate(json.loads(credential_json))
    if local_key and local_key.exists():
        return credentials.Certificate(str(local_key))
    return credentials.ApplicationDefault()


def initialize_firebase() -> firebase_admin.App:
    if firebase_admin._apps:
        return firebase_admin.get_app()

    with _firebase_lock:
        if firebase_admin._apps:
            return firebase_admin.get_app()
            
        settings = get_settings()
        credential = _load_credential()

        return firebase_admin.initialize_app(
            credential,
            {"projectId": settings.firebase_project_id},
        )


def get_firestore_client():
    initialize_firebase()
    return firestore.client()
