import pytest

from quantbot.order_ids import related_client_order_id, stable_client_order_id


def test_external_event_id_is_stable_ascii_and_leaves_algo_room():
    event_id = "pinets-20260916141S"
    first = stable_client_order_id("QBEX", event_id, "S", reserve=1)
    second = stable_client_order_id("QBEX", event_id, "S", reserve=1)
    assert first == second
    assert first.isascii() and first.isalnum()
    assert len(first) <= 31
    trailing = related_client_order_id(first, "T")
    assert trailing.isascii() and trailing.isalnum()
    assert len(trailing) <= 32


def test_punctuation_distinct_event_ids_do_not_collapse():
    dashed = stable_client_order_id("QBEX", "pinets-20260916141", "S", reserve=1)
    underscored = stable_client_order_id("QBEX", "pinets_20260916141", "S", reserve=1)
    assert dashed != underscored


@pytest.mark.parametrize("identity", ["事件-一", "---", "", "a" * 200])
def test_arbitrary_event_identity_still_produces_valid_id(identity):
    client_id = stable_client_order_id("QBEX", identity, "L", reserve=1)
    assert client_id.isascii() and client_id.isalnum()
    assert 1 <= len(client_id) <= 31


def test_invalid_client_id_is_rejected_before_related_id_generation():
    with pytest.raises(ValueError, match="ASCII alphanumeric"):
        related_client_order_id("pinets-invalid-id", "T")
