from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import dependencies


def test_dev_token_is_rejected_in_production(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(app_env="production", allow_dev_auth=True),
    )
    monkeypatch.setattr(dependencies, "initialize_firebase", lambda: None)
    monkeypatch.setattr(
        dependencies.auth,
        "verify_id_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid token")),
    )

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="dev:attacker")
    with pytest.raises(HTTPException) as exc_info:
        dependencies.get_current_user(credentials)

    assert exc_info.value.status_code == 401
