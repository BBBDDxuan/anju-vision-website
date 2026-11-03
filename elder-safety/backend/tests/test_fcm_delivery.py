"""Provider SDK fakes validate payloads/failures, not actual phone delivery."""
from types import SimpleNamespace
import sys

import pytest

from safety_backend.main import create_app
from safety_backend.providers import ProviderError


class Captured:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


def fake_firebase(monkeypatch, *, fail=False):
    sent = []
    def send(message, app):
        sent.append(message)
        if fail:
            raise RuntimeError("private-device-token-must-not-leak")
        return "provider-receipt-only"
    messaging = SimpleNamespace(**{name: Captured for name in (
        "Message", "Notification", "AndroidConfig", "AndroidNotification",
        "APNSConfig", "APNSPayload", "Aps")}, send=send)
    sdk = SimpleNamespace(messaging=messaging,
        credentials=SimpleNamespace(Certificate=lambda path: object()),
        initialize_app=lambda certificate, name: object())
    monkeypatch.setitem(sys.modules, "firebase_admin", sdk)
    return sent


def providers(settings, *, has_tokens=True):
    settings.push_mode = "fcm"
    settings.firebase_credentials = "test-only-credentials.json"
    app = create_app(settings)
    if has_tokens:
        app.state.repo.contact_tokens = lambda contact: [{"token": "private-test-token", "platform": "android"}]
    return app


def event():
    return {"event_id": "event_test", "event_type": "suspected_fall"}


def test_fcm_payload_has_sound_android_channel_and_only_event_metadata(settings, monkeypatch):
    sent = fake_firebase(monkeypatch)
    app = providers(settings)
    try:
        assert app.state.worker.providers.notify(event(), "primary") == "sent"
        payload = sent[0].kwargs
        assert payload["data"] == {"event_id": "event_test", "event_type": "suspected_fall", "contact_id": "primary"}
        android = payload["android"].kwargs
        assert android["priority"] == "high"
        assert android["ttl"].total_seconds() == 300
        assert android["notification"].kwargs == {
            "channel_id": "elder_safety_alerts_v1", "sound": "default", "tag": "event_test"}
        apns = payload["apns"].kwargs
        assert apns["headers"]["apns-push-type"] == "alert"
        assert apns["payload"].args[0].kwargs["sound"] == "default"
        assert "evidence" not in str(payload["data"])
    finally:
        app.state.repo.engine.dispose()


def test_fcm_failure_is_sanitized_and_not_a_receipt(settings, monkeypatch):
    sent = fake_firebase(monkeypatch, fail=True)
    app = providers(settings)
    try:
        with pytest.raises(ProviderError, match="^fcm_delivery_failed$"):
            app.state.worker.providers.notify(event(), "primary")
        assert len(sent) == 1
    finally:
        app.state.repo.engine.dispose()


def test_fcm_without_real_token_fails_without_external_sdk(settings, monkeypatch):
    sent = fake_firebase(monkeypatch)
    app = providers(settings, has_tokens=False)
    try:
        with pytest.raises(ProviderError, match="^fcm_no_registered_recipient$"):
            app.state.worker.providers.notify(event(), "primary")
        assert sent == []
    finally:
        app.state.repo.engine.dispose()


def test_notification_disabled_prevents_provider_send(settings, monkeypatch):
    sent = fake_firebase(monkeypatch)
    app = providers(settings)
    app.state.repo.get_preferences = lambda: {"notifications_enabled": False}
    try:
        assert app.state.worker.providers.notify(event(), "primary") == "skipped"
        assert sent == []
    finally:
        app.state.repo.engine.dispose()
