"""Weekly bookkeeping: which change events are news, and each roof's status (PLAN 4.5, 5)."""
from __future__ import annotations

import numpy as np
import pandas as pd

EVENT_KEY = ["roof_id", "date"]
SAME_EVENT_DAYS = 60


def _known_mask(events: pd.DataFrame, known: pd.DataFrame | None, same_event_days: int) -> np.ndarray:
    """True where `known` already has an event for the same roof within `same_event_days`.

    Late-arriving scenes can move a detected change date by a few weeks; that is the same change.
    """
    if known is None or known.empty or events.empty:
        return np.zeros(len(events), bool)
    m = events[EVENT_KEY].reset_index(drop=True).reset_index().merge(
        known[EVENT_KEY], on="roof_id", suffixes=("", "_k"))
    close = (m["date"] - m["date_k"]).abs() <= pd.Timedelta(days=same_event_days)
    hit = np.zeros(len(events), bool)
    hit[m.loc[close, "index"].to_numpy()] = True
    return hit


def new_events(events: pd.DataFrame, known: pd.DataFrame | None, run_date: pd.Timestamp,
               news_window_days: int = 60, same_event_days: int = SAME_EVENT_DAYS) -> pd.DataFrame:
    """Events not reported before and confirmed within `news_window_days` of `run_date`.

    Older unreported events (first run, archive backfill) are history, not news.
    """
    if events.empty:
        return events
    ev = events[~_known_mask(events, known, same_event_days)]
    recent = ev["confirmed_on"] >= run_date - pd.Timedelta(days=news_window_days)
    return ev[recent & (ev["confirmed_on"] <= run_date)].reset_index(drop=True)


def record_news(events: pd.DataFrame, known: pd.DataFrame | None, run_date: pd.Timestamp,
                news_window_days: int = 60, same_event_days: int = SAME_EVENT_DAYS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Update the event register and return (register, this run's news).

    The register keeps every change once, with `reported_on` = the run date that first
    listed it (NaT for old history). Re-running the same date returns the same news.
    """
    known = known.copy() if known is not None and not known.empty else events.iloc[0:0].assign(reported_on=pd.NaT)
    fresh = new_events(events, known, run_date, news_window_days, same_event_days)
    if not events.empty:
        unseen = events[~_known_mask(events, known, same_event_days)].assign(reported_on=pd.NaT)
        if len(fresh):
            f = pd.MultiIndex.from_frame(fresh[EVENT_KEY])
            unseen.loc[pd.MultiIndex.from_frame(unseen[EVENT_KEY]).isin(f), "reported_on"] = run_date
        known = pd.concat([known, unseen], ignore_index=True)
    known["reported_on"] = pd.to_datetime(known["reported_on"])
    news = known[known["reported_on"] == run_date].drop(columns="reported_on").reset_index(drop=True)
    return known.reset_index(drop=True), news


def roof_status(roof_ids: pd.Series, official_ac: set, scores: pd.Series, changed: set,
                threshold: float) -> pd.Series:
    """changed > official_ac > suspected (score >= threshold) > not_suspected."""
    out = []
    for r in roof_ids:
        if r in changed:
            out.append("changed")
        elif r in official_ac:
            out.append("official_ac")
        elif scores.get(r, 0.0) >= threshold:
            out.append("suspected")
        else:
            out.append("not_suspected")
    return pd.Series(out, index=roof_ids.index)
