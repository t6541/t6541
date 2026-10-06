from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import pandas as pd

@dataclass(frozen=True)
class ExternalExecutionSignal:
    event_id: str; source: str; direction: int; occurred_at: datetime
    price: float; stop_loss: float; take_profit: float; structure: str; indicators: dict

    def audit_payload(self) -> dict:
        return {"event_id": self.event_id, "source": self.source, "direction": self.direction,
                "occurred_at": self.occurred_at.isoformat(), "price": self.price,
                "stop_loss": self.stop_loss, "take_profit": self.take_profit,
                "structure": self.structure, "indicators": self.indicators}

def parse_external_signal(payload: dict, *, now: datetime | None = None,
                          maximum_age_seconds: int = 90) -> ExternalExecutionSignal:
    source = str(payload.get("source") or "").strip().lower()
    if source not in {"pinets", "webhook", "edge"}:
        raise ValueError("source must be pinets, webhook or edge")
    raw = str(payload.get("direction")).lower()
    direction = 1 if raw in {"1", "long", "buy", "多"} else -1 if raw in {"-1", "short", "sell", "空"} else 0
    if not direction: raise ValueError("explicit direction required")
    occurred = datetime.fromisoformat(str(payload.get("occurred_at")).replace("Z", "+00:00"))
    occurred = occurred.replace(tzinfo=timezone.utc) if occurred.tzinfo is None else occurred.astimezone(timezone.utc)
    age = ((now or datetime.now(timezone.utc)) - occurred).total_seconds()
    if age < -15 or age > maximum_age_seconds: raise ValueError("stale external signal")
    price, stop, target = (float(payload.get(k) or 0) for k in ("price", "stop_loss", "take_profit"))
    if price <= 0 or direction * (price-stop) <= 0 or direction * (target-price) <= 0:
        raise ValueError("invalid directional protection")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    event_id = str(payload.get("event_id") or sha256(canonical.encode()).hexdigest()[:24])
    indicators = payload.get("indicators") if isinstance(payload.get("indicators"), dict) else {}
    return ExternalExecutionSignal(event_id, source, direction, occurred, price, stop, target,
                                   str(payload.get("structure") or "external_indicator_trigger"), indicators)

def latest_external_signal(database: str | Path, *, now: datetime | None = None) -> ExternalExecutionSignal | None:
    inbox = Path(database).parent / "external-execution-signals.jsonl"
    if not inbox.exists(): return None
    valid = []
    for line in inbox.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
        try: valid.append(parse_external_signal(json.loads(line), now=now))
        except (ValueError, TypeError, json.JSONDecodeError): pass
    return max(valid, key=lambda item: item.occurred_at) if valid else None

def append_external_signal(database: str | Path, payload: dict) -> ExternalExecutionSignal:
    signal = parse_external_signal(payload)
    inbox = Path(database).parent / "external-execution-signals.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(signal.audit_payload(), ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
    return signal

def pinets_market_signal(one_minute: pd.DataFrame, five_minute: pd.DataFrame,
                         *, now: datetime | None = None) -> ExternalExecutionSignal | None:
    """Compact Pine-style EMA, MACD, RSI and momentum vote on live OKX candles."""
    one, five = one_minute.sort_values("date").tail(60), five_minute.sort_values("date").tail(60)
    if len(one) < 30 or len(five) < 30: return None
    votes = []
    for frame in (one, five):
        close = frame["close"].astype(float)
        ema5 = close.ewm(span=5, adjust=False).mean(); ema20 = close.ewm(span=20, adjust=False).mean()
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        delta = close.diff(); gain = delta.clip(lower=0).rolling(14).mean(); loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
        votes.extend((1 if ema5.iloc[-1] > ema20.iloc[-1] else -1,
                      1 if macd.iloc[-1] > macd.iloc[-2] else -1,
                      1 if float(rsi.iloc[-1]) >= 52 else -1 if float(rsi.iloc[-1]) <= 48 else 0,
                      1 if close.iloc[-1] > close.iloc[-2] else -1))
    score = sum(votes)
    if abs(score) < 4: return None
    direction = 1 if score > 0 else -1; price = float(one.iloc[-1]["close"]); recent = one.tail(8)
    stop = float(recent["low"].astype(float).min() if direction > 0 else recent["high"].astype(float).max())
    risk = direction * (price - stop)
    if risk <= 0: return None
    target = price + direction * max(risk * 1.2, price * .001)
    bar_time = pd.Timestamp(one.iloc[-1]["date"]); occurred = bar_time.to_pydatetime()
    if occurred.tzinfo is None: occurred = occurred.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if (current - occurred.astimezone(timezone.utc)).total_seconds() > 90: return None
    return ExternalExecutionSignal(f"pinets-{bar_time.strftime('%Y%m%d%H%M')}-{direction}",
        "pinets", direction, occurred, price, stop, target,
        "pine_ema_macd_rsi_momentum_consensus", {"votes": votes, "score": score, "required_abs_score": 4})
