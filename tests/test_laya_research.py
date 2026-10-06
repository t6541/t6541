import json

from quantbot.laya_research import LayaResearchClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_laya_analysis_is_read_only_and_uses_systemone_endpoint():
    seen = {}

    def opener(request, timeout):
        seen.update(url=request.full_url, timeout=timeout,
                    body=json.loads(request.data.decode()))
        return _Response({"choice": "observe", "confidence": 0.61})

    result = LayaResearchClient(opener=opener).analyze(
        instrument="ETH-USDT-SWAP", state={"price": 100},
        questions={"direction": ["bullish", "bearish", "unclear"]},
        event_id="test-laya-1", event_time="2026-09-25T00:00:00+00:00",
    )
    assert seen["url"] == "http://127.0.0.1:8000/v1/systemone"
    assert seen["body"]["state"]["price"] == 100
    assert result.event_id == "test-laya-1"
    assert result.decision_eligible is False


def test_persist_forces_non_decisional_payload():
    calls = []

    class Store:
        def record_external_research_signal(self, **kwargs):
            calls.append(kwargs)
            return True

    result = LayaResearchClient(opener=lambda *_args, **_kwargs: _Response({})).analyze(
        instrument="BTC-USDT-SWAP", state="sample", questions={}, event_id="e1",
    )
    assert LayaResearchClient.persist(Store(), result) is True
    assert calls[0]["source"] == "laya"
    assert calls[0]["payload"]["decision_eligible"] is False
