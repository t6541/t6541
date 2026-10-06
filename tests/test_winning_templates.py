import pytest

from quantbot.winning_templates import winning_template_for


@pytest.mark.parametrize("direction,category,code,count", [
    (1, "true_endpoint_reversal", "bottom_reversal_long", 21),
    (-1, "local_endpoint_reversal", "top_reversal_short", 24),
    (1, "current_timeframe_trend_continuation", "pullback_long", 15),
    (-1, "higher_timeframe_trend_continuation", "throwback_short", 17),
])
def test_frozen_profitable_structure_identity(direction, category, code, count):
    result = winning_template_for({"direction": direction, "category": category})
    assert result.code == code
    assert result.evidence_count == count
