from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import httpx
import pytest

from backend.db import Database
from backend.errors import AppError
from backend.providers.qwen import QwenProvider


def arguments(tmp_path):
    image = tmp_path / "test.jpg"
    image.write_bytes(b"TEST_IMAGE_BYTES_NOT_REAL_VISION")
    return {"window": {"frames": [{"id": "f0", "pts_ms": 0, "path": str(image)}]},
            "entities": [], "video": {"id": "unit-test-video"}, "job_id": "unit-test-job", "output_tokens": 1000}


def response(content=None, **kwargs):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content or json.dumps({"entities": [], "events": []})}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10}, **kwargs}


def configured(settings):
    return replace(settings, api_key="test-only-secret", enable_network=True, cost_confirmed=True, input_price=1, output_price=2)


def test_qwen_multiframe_request_and_usage_are_real_http_contract_only(settings, tmp_path):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-only-secret"
        return httpx.Response(200, json=response())

    provider = QwenProvider(configured(settings), Database(settings.data_dir), httpx.MockTransport(handler))
    result = provider.extract(**arguments(tmp_path))
    assert result.extraction.events == []
    payload = captured[0]
    assert payload["model"] == settings.model and payload["enable_thinking"] is False
    assert payload["messages"][0]["content"][-1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    with provider.db.connect() as c:
        charge = c.execute("SELECT reserved,actual FROM charges").fetchone()
    assert charge[0] > charge[1] > 0


@pytest.mark.parametrize("flag", ["key", "network", "confirmation", "price"])
def test_unconfirmed_configuration_never_sends_request(settings, tmp_path, flag):
    base = configured(settings)
    changes = {"key": {"api_key": ""}, "network": {"enable_network": False}, "confirmation": {"cost_confirmed": False}, "price": {"input_price": 0}}[flag]
    provider = QwenProvider(replace(base, **changes), Database(settings.data_dir), httpx.MockTransport(lambda r: pytest.fail("Network must not be called")))
    with pytest.raises(AppError): provider.extract(**arguments(tmp_path))


@pytest.mark.parametrize("fault", ["timeout", "auth", "invalid_json", "truncated", "token_overflow"])
def test_provider_failures_are_sanitized_and_no_auto_retry(settings, tmp_path, fault):
    calls = []

    def handler(request):
        calls.append(1)
        if fault == "timeout": raise httpx.ReadTimeout("test-only-secret", request=request)
        if fault == "auth": return httpx.Response(401, text="echo test-only-secret")
        body = response("not json" if fault == "invalid_json" else None)
        if fault == "truncated": body["choices"][0]["finish_reason"] = "length"
        if fault == "token_overflow": body["usage"]["prompt_tokens"] = 1000000
        return httpx.Response(200, json=body)

    provider = QwenProvider(configured(settings), Database(settings.data_dir), httpx.MockTransport(handler))
    with pytest.raises(AppError) as caught: provider.extract(**arguments(tmp_path))
    assert len(calls) == 1 and "test-only-secret" not in str(caught.value.public())


def test_cost_limit_atomic_across_concurrent_requests(settings):
    db = Database(settings.data_dir)

    def reserve(_):
        try:
            db.reserve("vid", "job", .6, 1, 1)
            return True
        except AppError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(reserve, range(2))) == 1


def test_settings_reject_nonfinite_prices(settings):
    with pytest.raises(ValueError): replace(settings, input_price=float("nan"))


def test_explicit_request_limited_mode_does_not_claim_rmb_cost(settings, tmp_path):
    settings = replace(settings, api_key='test-only-secret', enable_network=True, billing_mode='request_limited', max_daily_calls=1)
    db = Database(settings.data_dir)
    provider = QwenProvider(settings, db, httpx.MockTransport(lambda r: httpx.Response(200, json=response())))
    result = provider.extract(**arguments(tmp_path))
    assert result.usage['rmb_cost'] is None and db.usage_summary()['requests'] == 1
    with pytest.raises(AppError) as caught:
        provider.extract(**arguments(tmp_path))
    assert caught.value.code == 'REQUEST_LIMIT'
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM charges').fetchone()[0] == 0


def test_provider_error_diagnostic_is_retained_without_secret(settings, tmp_path):
    provider = QwenProvider(configured(settings), Database(settings.data_dir), httpx.MockTransport(
        lambda request: httpx.Response(400, json={'error': {'message': 'JSON required; echo test-only-secret'}})))
    with pytest.raises(AppError):
        provider.extract(**arguments(tmp_path))
    record = next((settings.data_dir / 'runs/unit-test-job').glob('error-*.json')).read_text(encoding='utf8')
    assert 'JSON required' in record and 'test-only-secret' not in record and '[REDACTED]' in record


def test_visual_verification_sends_json_mode_and_prompt_fingerprint(settings, tmp_path):
    captured = []
    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        assert payload['response_format'] == {'type': 'json_object'}
        assert 'JSON' in payload['messages'][0]['content'][0]['text']
        return httpx.Response(200, json=response('{"verdict":"uncertain"}'))
    provider = QwenProvider(configured(settings), Database(settings.data_dir), httpx.MockTransport(handler))
    args = arguments(tmp_path)
    raw, usage = provider.verify_event(instructions='严格返回JSON对象，不要推测。', frames=args['window']['frames'],
                                      video=args['video'], job_id=args['job_id'], regions=[], focus=None)
    assert len(captured) == 1 and json.loads(raw)['verdict'] == 'uncertain'
    assert usage['kind'] == 'verification' and len(usage['prompt_sha256']) == 64
