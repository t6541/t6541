import json
import threading
from copy import deepcopy

import pandas as pd
import pytest

from quantbot.pullback_execution import (
    PULLBACK_TTL_SECONDS, near_pullback_quote, register_pullback, poll_pullback,
    monitor_pullbacks)
from quantbot.state import StateStore
from quantbot.validation_execution import (
    MISSED_MA5_PULLBACK_TTL_SECONDS, expired_missed_ma5_pullback_orders,
    latest_local_reversal_extreme)


def plan(direction=-1):
    return dict(instrument="ETH-USDT-SWAP", direction=direction, entry=2467.68,
                stop=2467.68 - direction * 1.6, target=2467.68 + direction * 12,
                atr=1.2, size="0.05", version="test", signal_time="2026-09-10T11:43:00",
                expires_ms=400000)


def quote(p, price, ts=102000):
    return dict(instId=p["instrument"], ts=str(ts),
                bidPx=str(price if p["direction"] < 0 else price-.02),
                askPx=str(price+.02 if p["direction"] < 0 else price))


@pytest.mark.parametrize("direction", [-1, 1])
def test_near_limit_needs_real_approach_on_executable_side(direction):
    p = plan(direction); price=p["entry"]+direction*.11
    assert near_pullback_quote(p, quote(p,price),102000,price+direction*.1,101000)[0]
    assert not near_pullback_quote(p,quote(p,price),102000,price-direction*.1,101000)[0]
    assert not near_pullback_quote(p,quote(p,price),102000,price,101000)[0]
    assert not near_pullback_quote(p,quote(p,price),102000)[0]


@pytest.mark.parametrize("change", ["far", "touched", "stale", "spread", "future", "nan", "wrong", "expired", "cost", "risk"])
def test_bad_quote_or_insufficient_frozen_space_never_converts(change):
    p=plan(); q=quote(p,p["entry"]-.11)
    if change == "far": q=quote(p,p["entry"]-.3)
    if change == "touched": q=quote(p,p["entry"])
    if change == "stale": q["ts"]="99000"
    if change == "future": q["ts"]="102001"
    if change == "spread": q["askPx"]=str(float(q["bidPx"])+.5)
    if change == "nan": q["bidPx"]="NaN"
    if change == "wrong": q["instId"]="BTC-USDT-SWAP"
    if change == "expired": p["expires_ms"]=102000
    if change == "cost": p["target"]=p["entry"]-2.5
    if change == "risk": p["stop"]=p["entry"]+5
    assert not near_pullback_quote(p,q,102000,p["entry"]-.3,101000)[0]


class FakeClient:
    def __init__(self,p):
        self.p=p; self.calls=[]; self.canceled=False; self.market_calls=0
        self.final_state="canceled"; self.final_fills="0"; self.initial_fills="0"
        self.ack="0"; self.uncertain=False; self.after_price=None; self.exposure=False
        self.order=dict(ordId="old",clOrdId="QBVALPB20260910114300S",
            instId=p["instrument"],side="sell" if p["direction"]<0 else "buy",
            posSide="short" if p["direction"]<0 else "long",sz="0.05",px=str(p["entry"]),cTime="100000")
    def _request(self,method,path,payload,private=False):
        self.calls.append(path)
        if path.endswith("/order"):
            return {"code":"0","data":[{**self.order,
                "state":self.final_state if self.canceled else "live",
                "accFillSz":self.final_fills if self.canceled else self.initial_fills}]}
        price=self.p["entry"]+self.p["direction"]*.11
        if self.canceled and self.after_price is not None:price=self.after_price
        return {"code":"0","data":[quote(self.p,price)]}
    def safety_snapshot(self):
        self.calls.append("snapshot")
        return dict(positions=[{"pos":".05"}] if self.canceled and self.exposure else [],
                    orders=[] if self.canceled else [self.order])
    def cancel_orders(self,orders):
        self.calls.append("cancel");self.canceled=True
        return {"code":"0","data":[{"ordId":"old","sCode":self.ack}]}
    def place_demo_market_order(self,*args,**kwargs):
        self.calls.append("market");self.market_calls+=1;self.sent=(args,kwargs)
        if self.uncertain:raise TimeoutError("unknown POST result")
        return {"code":"0","data":[{"ordId":"new","sCode":"0"}]}


def setup_case(tmp_path,direction=-1):
    s=StateStore(tmp_path/"test.db");p=plan(direction);c=FakeClient(p)
    register_pullback(s,client_id=c.order["clOrdId"],order_id="old",instrument=p["instrument"],
        direction=direction,entry=p["entry"],stop=p["stop"],target=p["target"],atr=p["atr"],
        size=p["size"],version=p["version"],signal_time=p["signal_time"],created_ms=100000)
    s.connection.execute("UPDATE ma5_pullback_execution SET previous_price=?,previous_ts=101000",
                         (p["entry"]+direction*.3,));s.connection.commit()
    return s,p,c


def run(s,c,can_enter=lambda:True,stopped=lambda:False,now=102000):
    row=s.connection.execute("SELECT * FROM ma5_pullback_execution").fetchone()
    return poll_pullback(c,s,row,can_enter=can_enter,stopped=stopped,clock_ms=lambda:now)


@pytest.mark.parametrize("direction",[-1,1])
def test_exact_zero_fill_cancel_then_one_market_preserves_protection(tmp_path,direction):
    s,p,c=setup_case(tmp_path,direction)
    assert run(s,c).action=="submitted"
    assert c.calls.index("cancel") < c.calls.index("market")
    assert c.calls[c.calls.index("cancel")+1]=="/api/v5/trade/order"
    args,kw=c.sent
    assert args[1:]==(1,str(p["stop"]))
    assert kw["take_profit_price"]==str(p["target"])
    assert kw["client_order_id"].endswith("M")
    assert len(kw["client_order_id"])<=32
    assert s.connection.execute("SELECT count(*) FROM trade_lifecycle").fetchone()[0]==1
    assert not s.connection.execute("SELECT * FROM ma5_pullback_execution WHERE state='armed'").fetchall()
    s.close()


@pytest.mark.parametrize("case",["initial_partial","partial","filled","not_terminal","rejected","exposure","away","stop","risk","identity","missing_fill","nan_fill","timeout"])
def test_races_and_unknown_results_never_duplicate(tmp_path,case):
    s,p,c=setup_case(tmp_path)
    if case=="initial_partial":c.initial_fills=".01"
    if case=="partial":c.final_fills=".01"
    if case=="filled":c.final_state="filled";c.final_fills=".05"
    if case=="not_terminal":c.final_state="live"
    if case=="rejected":c.ack="51400"
    if case=="exposure":c.exposure=True
    if case=="away":c.after_price=p["entry"]-1
    if case=="identity":c.order["sz"]=".10"
    if case=="missing_fill":c.final_fills=""
    if case=="nan_fill":c.final_fills="NaN"
    if case=="timeout":c.uncertain=True
    checks=iter([True,False]) if case=="risk" else None
    stop_checks=iter([False,False,True]) if case=="stop" else None
    try:
        run(s,c,can_enter=(lambda:next(checks)) if checks else lambda:True,
            stopped=(lambda:next(stop_checks)) if stop_checks else lambda:False)
    except (ValueError,TimeoutError):
        assert case in {"identity","missing_fill","nan_fill","timeout"}
    assert c.market_calls==(1 if case=="timeout" else 0)
    if case=="timeout":
        s.close();reopened=StateStore(tmp_path/"test.db")
        assert reopened.connection.execute("SELECT state FROM ma5_pullback_execution").fetchone()[0]=="submitting"
        reopened.close()
        result=monitor_pullbacks(c,tmp_path/"test.db",stop_event=threading.Event(),can_enter=lambda:True)
        assert result.action=="observe" and c.market_calls==1
    else:s.close()


def test_default_five_minute_lifetime_does_not_expire_at_two_minutes():
    assert MISSED_MA5_PULLBACK_TTL_SECONDS==PULLBACK_TTL_SECONDS==300
    snapshot={"orders":[{"clOrdId":"QBVALPB1","ordId":"old","cTime":"100000"},
                        {"clOrdId":"MANUAL","ordId":"manual","cTime":"100000"}]}
    assert not expired_missed_ma5_pullback_orders(snapshot,now_ms=220001)
    assert not expired_missed_ma5_pullback_orders(snapshot,now_ms=399999)
    assert [r["ordId"] for r in expired_missed_ma5_pullback_orders(snapshot,now_ms=400000)]==["old"]


def test_historical_point_eleven_gap_keeps_original_target_and_covers_taker_costs():
    p=plan();p["target"]=2458.68
    allowed,price,_=near_pullback_quote(p,quote(p,2467.57),102000,2467.40,101000)
    assert allowed and price==2467.57
    # A finished 1m high is not itself sufficient: absent executable quotes
    # and their order in time, the historical bar cannot authorize a trade.
    assert not near_pullback_quote(p,quote(p,2467.57),102000)[0]


def test_original_limit_fill_opens_durable_lifecycle(tmp_path):
    s,p,c=setup_case(tmp_path)
    c.initial_fills=".05"
    c.order["avgPx"] = str(p["entry"])
    result = run(s,c)
    assert result.action == "manage"
    row = s.connection.execute("SELECT * FROM trade_lifecycle").fetchone()
    assert row["trade_uid"] == c.order["clOrdId"]
    assert row["order_id"] == "old"
    assert row["branch"] == "missed_ma5_pullback_limit_fill"
    assert json.loads(row["signal_context_json"])["winning_trigger_template"] == "throwback_short"
    s.close()


def test_watch_expires_at_five_minutes_and_only_cancels_original(tmp_path):
    s,p,c=setup_case(tmp_path)
    assert run(s,c,now=400000).action=="expired"
    assert c.canceled and c.market_calls==0
    s.close()


@pytest.mark.parametrize("direction",[-1,1])
def test_latest_pivot_owns_stop_not_remote_six_candle_extreme(direction):
    values=[110,105,103,106,104,102]
    if direction>0:values=[200-v for v in values]
    values=[104 if direction<0 else 96]*20+values
    frame=pd.DataFrame({"date":pd.date_range("2026-09-10",periods=len(values),freq="min"),
                        "high":values,"low":values,"close":values})
    assert latest_local_reversal_extreme(frame,direction)==(106 if direction<0 else 94)
    frame.loc[25,"high" if direction<0 else "low"]=111 if direction<0 else 89
    assert latest_local_reversal_extreme(frame,direction)==(106 if direction<0 else 94)
    frame.loc[25,"close"]=111 if direction<0 else 89
    assert latest_local_reversal_extreme(frame,direction)==(111 if direction<0 else 89)
