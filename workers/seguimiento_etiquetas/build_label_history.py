"""Build normalized label events and elapsed time between labels."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")

import pandas as pd

try:  # Package import when embedded; fallback keeps direct CLI execution.
    from .label_config import display_datetime, parse_label_comment
except ImportError:  # pragma: no cover - exercised by CLI/tests from this directory
    from label_config import display_datetime, parse_label_comment


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "output" / "tracking_all.parquet"
DEFAULT_OUTPUT = HERE / "output"


def parse_tracking_datetime(value: object) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def normalize_identifier(value: object) -> object:
    """Unify integral numeric/string IDs while retaining nonnumeric IDs."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return int(text[:-2])
    return value


def stable_identifier_sort_key(value: object) -> str:
    normalized = normalize_identifier(value)
    if isinstance(normalized, int):
        sign = "0" if normalized >= 0 else "-"
        return f"0:{sign}:{abs(normalized):030d}"
    return f"1:{'' if normalized is None else str(normalized)}"


REDACTED_SEED_KINDS = {"historical_certified_seed", "historical_seed"}


def _is_redacted_evidence(source: object, observation_kind: str) -> bool:
    """A snapshot row whose comment was redacted before distribution.

    These rows exist to prove an OT was swept, not to carry a label.  Counting
    them as "unrecognized" would drown the quality signal: the packaged seed
    alone contributes 9.385 of them.
    """
    if observation_kind not in REDACTED_SEED_KINDS:
        return False
    comment = source.get("tracking_comment") if hasattr(source, "get") else None
    try:
        if comment is not None and pd.isna(comment):
            comment = None
    except (TypeError, ValueError):
        pass
    return comment is None or not str(comment).strip()


def build_events(
    df: pd.DataFrame, window_sla_minutes: float = 5.0
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    rows: list[dict] = []
    rejected: list[dict] = []
    stats = {
        "tracking_rows": len(df),
        "recognized": 0,
        "unrecognized": 0,
        "explicit_dates": 0,
        "redacted_evidence": 0,
        "stale_window_downgrades": 0,
    }
    for _, source in df.iterrows():
        raw_kind = source.get("tracking_observation_kind")
        try:
            if raw_kind is not None and pd.isna(raw_kind):
                raw_kind = None
        except (TypeError, ValueError):
            pass
        if _is_redacted_evidence(source, str(raw_kind or "")):
            stats["redacted_evidence"] += 1
            continue
        parsed = parse_label_comment(source.get("tracking_comment"))
        if not parsed:
            stats["unrecognized"] += 1
            rejected.append({
                "work_order_number": source.get("work_order_number"),
                "vehicle_code": source.get("vehicle_code"),
                "client": source.get("client"),
                "cd": source.get("cd"),
                "tracking_id": source.get("tracking_id"),
                "tracking_date": source.get("tracking_date"),
                "tracking_comment": source.get("tracking_comment"),
                "created_by_id": source.get("created_by_id"),
                "created_by_name": source.get("created_by_name"),
                "rejection_reason": "No coincide con ID o texto exacto de etiqueta controlada",
            })
            continue
        label_id, label_name, explicit_at, explicit_display = parsed
        tracking_at = parse_tracking_datetime(source.get("tracking_date"))
        observed_at = parse_tracking_datetime(source.get("tracking_observed_at"))
        observed_from = parse_tracking_datetime(source.get("tracking_observed_from"))
        observation_kind_value = source.get("tracking_observation_kind")
        if observation_kind_value is None or pd.isna(observation_kind_value):
            observation_kind_value = source.get("observation_kind")
        if observation_kind_value is None or pd.isna(observation_kind_value):
            observation_kind_value = ""
        observation_kind = str(observation_kind_value)
        # Rows written by the earlier worker have observed_at but no kind. They
        # are still real API observations (not parquet seeds), including OT 5508.
        is_live_observation = observation_kind.startswith("live") or (
            not observation_kind and observed_at is not None
        )
        # A window wider than the nominal SLA means the ceiling stopped being a
        # useful hour.  Combined with a trackingDate on an earlier local day, it
        # proves the event did not happen inside this window, so sealing it at
        # the ceiling would invent an hour and collapse durations to zero.  The
        # width test keeps a genuine five-minute observation that merely
        # straddles midnight from being downgraded to day precision.
        window_minutes = (
            (observed_at - observed_from).total_seconds() / 60.0
            if (observed_at and observed_from)
            else None
        )
        window_lost = observed_from is None or (
            window_minutes is not None and window_minutes > window_sla_minutes
        )
        if (
            is_live_observation
            and window_lost
            and observed_at is not None
            and tracking_at is not None
            and tracking_at.astimezone(BOGOTA).date() < observed_at.astimezone(BOGOTA).date()
        ):
            is_live_observation = False
            stats["stale_window_downgrades"] += 1
        if explicit_at:
            event_at = explicit_at
            event_source = "comment_explicit"
            event_time_quality = "explicit"
        elif observed_at and is_live_observation:
            event_at = observed_at
            event_source = "observation_window_end"
            event_time_quality = "live_window" if observed_from else "live_window_unbounded"
        else:
            event_at = tracking_at
            event_source = "tracking_date_baseline"
            event_time_quality = "historical_date"
        if event_at is None:
            stats["unrecognized"] += 1
            rejected.append({
                "work_order_number": source.get("work_order_number"),
                "vehicle_code": source.get("vehicle_code"),
                "client": source.get("client"),
                "cd": source.get("cd"),
                "tracking_id": source.get("tracking_id"),
                "tracking_date": source.get("tracking_date"),
                "tracking_comment": source.get("tracking_comment"),
                "created_by_id": source.get("created_by_id"),
                "created_by_name": source.get("created_by_name"),
                "rejection_reason": "Etiqueta valida sin timestamp historico ni ventana de observacion",
            })
            continue
        stats["recognized"] += 1
        if explicit_at:
            stats["explicit_dates"] += 1
        rows.append({
            "work_order_number": normalize_identifier(source.get("work_order_number")),
            "vehicle_code": source.get("vehicle_code"),
            "client": source.get("client"),
            "cd": source.get("cd"),
            "work_order_status": source.get("work_order_status"),
            "work_order_type": source.get("work_order_type"),
            "tracking_id": normalize_identifier(source.get("tracking_id")),
            "tracking_comment": source.get("tracking_comment"),
            "created_by_id": source.get("created_by_id"),
            "created_by_name": source.get("created_by_name"),
            "label_id": label_id,
            "label_name": label_name,
            "event_at": to_iso(event_at),
            "event_at_display": (
                event_at.astimezone(ZoneInfo("America/Bogota")).strftime("%d/%m/%Y")
                if event_time_quality == "historical_date"
                else display_datetime(event_at)
            ),
            "event_at_source": event_source,
            "event_time_quality": event_time_quality,
            "observation_kind": observation_kind or ("legacy_worker_observation" if observed_at else "historical_seed"),
            "observation_from": to_iso(observed_from),
            "observation_to": to_iso(observed_at),
            "observation_window_minutes": round((observed_at - observed_from).total_seconds() / 60, 2) if observed_at and observed_from else None,
            "explicit_date_display": explicit_display,
        })
    return pd.DataFrame(rows), pd.DataFrame(rejected), stats


def build_transitions(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty:
        empty = pd.DataFrame()
        return empty, empty
    events = events.copy()
    # `event_at` mixes sources with different precision: a window ceiling carries
    # microseconds, a trackingDate baseline does not.  Without an explicit
    # format pandas infers one from the first row and then raises on the rest,
    # which took down a whole live cycle the moment both kinds coexisted.
    events["event_at_dt"] = pd.to_datetime(events["event_at"], utc=True, format="ISO8601")
    events["_work_order_sort_key"] = events["work_order_number"].map(stable_identifier_sort_key)
    events["_tracking_sort_key"] = events["tracking_id"].map(stable_identifier_sort_key)
    events = events.sort_values(["_work_order_sort_key", "event_at_dt", "_tracking_sort_key"])
    raw_previous_label = events.groupby("_work_order_sort_key")["label_id"].shift(1)
    events["is_label_change"] = raw_previous_label.isna() | (raw_previous_label != events["label_id"])
    transitions = events[events["is_label_change"]].copy()
    # Recompute on the filtered transition stream. Reaffirming A must not reset
    # the residence clock used later for A -> B.
    transitions["previous_label_id"] = transitions.groupby("_work_order_sort_key")["label_id"].shift(1)
    transitions["previous_label_name"] = transitions.groupby("_work_order_sort_key")["label_name"].shift(1)
    transitions["previous_event_time_quality"] = transitions.groupby("_work_order_sort_key")["event_time_quality"].shift(1)
    transitions["minutes_since_previous_event"] = transitions.groupby("_work_order_sort_key")["event_at_dt"].diff().dt.total_seconds().div(60).round(2)
    transitions["transition"] = transitions.apply(
        lambda row: f"{'Inicio' if pd.isna(row['previous_label_name']) else row['previous_label_name']} -> {row['label_name']}", axis=1
    )
    transitions["time_to_label_minutes"] = transitions["minutes_since_previous_event"]
    transitions = transitions.drop(columns=["event_at_dt", "_tracking_sort_key"])

    durations = transitions.copy()
    durations["segment_start_at"] = pd.to_datetime(
        durations["event_at"], utc=True, format="ISO8601"
    )
    durations["segment_end_at"] = durations.groupby("_work_order_sort_key")["segment_start_at"].shift(-1)
    durations["duration_hours"] = (
        durations["segment_end_at"] - durations["segment_start_at"]
    ).dt.total_seconds().div(3600).round(2)
    durations["duration_is_open"] = durations["segment_end_at"].isna()
    durations["segment_end_time_quality"] = durations.groupby("_work_order_sort_key")["event_time_quality"].shift(-1)
    durations["time_quality"] = durations.apply(_duration_quality, axis=1)
    durations["duration_is_precise"] = durations.apply(
        lambda row: (
            not bool(row["duration_is_open"])
            and row.get("event_time_quality") in {"explicit", "live_window"}
            and row.get("segment_end_time_quality") in {"explicit", "live_window"}
        ),
        axis=1,
    )
    start_display = durations["segment_start_at"].dt.tz_convert("America/Bogota").dt.strftime("%d/%m/%Y %H:%M")
    end_display = durations["segment_end_at"].dt.tz_convert("America/Bogota").dt.strftime("%d/%m/%Y %H:%M")
    durations["segment_start_display"] = start_display.where(
        durations["event_time_quality"] != "historical_date",
        start_display.str.slice(0, 10),
    )
    durations["segment_end_display"] = end_display.where(
        durations["segment_end_time_quality"] != "historical_date",
        end_display.str.slice(0, 10),
    )
    durations = durations.drop(columns=["segment_start_at", "segment_end_at", "_work_order_sort_key"])
    transitions = transitions.drop(columns=["_work_order_sort_key"])
    return transitions, durations


def _duration_quality(row: pd.Series) -> str:
    start = row.get("event_time_quality")
    end = row.get("segment_end_time_quality")
    if pd.isna(end):
        return str(start) if start in {"explicit", "live_window", "historical_date"} else "mixed"
    if start == end and start in {"explicit", "live_window", "historical_date"}:
        return str(start)
    return "mixed"


def build_summary(durations: pd.DataFrame) -> pd.DataFrame:
    if durations.empty:
        return pd.DataFrame()
    closed = durations[durations["duration_hours"].notna()].copy()
    if closed.empty:
        return pd.DataFrame()
    summary = closed.groupby(["label_id", "label_name"], as_index=False).agg(
        segments=("duration_hours", "count"),
        average_hours=("duration_hours", "mean"),
        median_hours=("duration_hours", "median"),
        min_hours=("duration_hours", "min"),
        max_hours=("duration_hours", "max"),
    )
    for col in ("average_hours", "median_hours", "min_hours", "max_hours"):
        summary[col] = summary[col].round(2)
    return summary.sort_values("label_id")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = pd.read_parquet(args.input)
    events, rejected, stats = build_events(source)
    transitions, durations = build_transitions(events)
    summary = build_summary(durations)
    events.to_parquet(output_dir / "label_events.parquet", index=False)
    transitions.to_parquet(output_dir / "label_transitions.parquet", index=False)
    durations.to_parquet(output_dir / "label_durations.parquet", index=False)
    summary.to_parquet(output_dir / "label_duration_summary.parquet", index=False)
    rejected.to_parquet(output_dir / "unrecognized_tracking.parquet", index=False)
    events.to_csv(output_dir / "label_events.csv", index=False, encoding="utf-8-sig")
    rejected.to_csv(output_dir / "unrecognized_tracking.csv", index=False, encoding="utf-8-sig")
    (output_dir / "label_processing_summary.json").write_text(json.dumps({**stats, "generated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**stats, "transitions": len(transitions), "duration_segments": len(durations), "summary_rows": len(summary)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
