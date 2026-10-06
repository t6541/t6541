from quantbot.live_fault_journal import write_live_fault


def test_live_fault_records_exception_origin_for_offline_diagnosis(tmp_path):
    try:
        value = None
        first, second = value
    except TypeError as exc:
        path = write_live_fault(tmp_path, "aggressive", exc)
    text = path.read_text(encoding="utf-8")
    assert "test_live_fault_records_exception_origin_for_offline_diagnosis" in text
    assert "cannot unpack non-iterable NoneType object" in text
    assert "aggressive" in text
