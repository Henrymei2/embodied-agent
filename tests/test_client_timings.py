"""Part 7: the client-reported stages that make the product budget computable."""

from __future__ import annotations

import pytest

from src.open_llm_vtuber.latency import (
    MAX_CLIENT_CAPTURE_MS,
    SPEECH_TO_FIRST_AUDIO,
    ClientTurnTimings,
    LatencyRecorder,
    TurnLatency,
    TurnTimer,
    client_timing_is_plausible,
    parse_client_timings,
)


def test_a_well_formed_message_yields_both_stages():
    accepted = parse_client_timings({"capture_ms": 1800.0, "endpoint_ms": 220.0})
    assert accepted == {"capture": 1800.0, "endpoint": 220.0}


@pytest.mark.parametrize(
    "payload",
    [
        {"capture_ms": -5},
        {"capture_ms": float("nan")},
        {"capture_ms": float("inf")},
        {"capture_ms": "fast"},
        {"capture_ms": True},
        {"capture_ms": MAX_CLIENT_CAPTURE_MS + 1},
        {"endpoint_ms": 60_000},
        "not a message",
        None,
    ],
)
def test_an_implausible_timing_is_dropped_rather_than_clamped(payload):
    """Clamping would file a fabricated number as a real measurement.

    Dropping leaves the stage absent, and absent is a thing the report already
    knows how to say — Part 2 settled that an unmeasured number is not zero.
    """
    assert parse_client_timings(payload) == {}


def test_a_client_cannot_report_a_stage_the_server_measures():
    """A message naming `first_audio` must not overwrite a server figure."""
    assert parse_client_timings({"first_audio_ms": 1.0}) == {}
    assert not client_timing_is_plausible("first_audio", 1.0)
    assert not client_timing_is_plausible("asr", 1.0)


def test_a_client_timing_can_only_add_time_never_subtract_it():
    """The only leverage a client has is making its own turn look slower."""
    for value in (0.0, 1.0, MAX_CLIENT_CAPTURE_MS):
        assert client_timing_is_plausible("capture", value)
    assert not client_timing_is_plausible("capture", -0.0001)


def test_timings_are_consumed_by_the_turn_that_takes_them():
    """One report must not be counted against several turns."""
    pending = ClientTurnTimings()
    pending.offer("client-a", {"capture": 100.0})
    assert pending.take("client-a") == {"capture": 100.0}
    assert pending.take("client-a") == {}


def test_a_later_report_replaces_an_earlier_one_rather_than_accumulating():
    pending = ClientTurnTimings()
    pending.offer("client-a", {"capture": 100.0})
    pending.offer("client-a", {"capture": 250.0})
    assert pending.take("client-a") == {"capture": 250.0}
    assert pending.pending_clients() == 0


def test_a_disconnected_client_leaves_nothing_behind():
    """The unbounded-growth defect of slice 7.2, arriving one connection at a time."""
    pending = ClientTurnTimings()
    for index in range(50):
        pending.offer(f"client-{index}", {"capture": 10.0})
    assert pending.pending_clients() == 50
    for index in range(50):
        pending.forget(f"client-{index}")
    assert pending.pending_clients() == 0


def test_setting_a_stage_does_not_charge_the_next_stage_with_browser_time():
    """`set_stage` files a duration; it must not move the pipeline cursor."""
    # Two ticks: one for the timer's starting mark, one for `complete`.
    # `set_stage` consumes none, which is the property under test.
    ticks = iter([0.0, 0.5])
    timer = TurnTimer("turn-1", _clock=lambda: next(ticks))
    timer.set_stage("capture", 1800.0)
    elapsed = timer.complete("asr")
    assert elapsed == pytest.approx(500.0)
    stages = {sample.stage: sample.milliseconds for sample in timer.samples()}
    assert stages["capture"] == pytest.approx(1800.0)


def test_the_end_to_end_figure_becomes_computable_once_every_stage_reports():
    """The whole point of the slice.

    Before this, `capture`, `endpoint`, and `retrieve` had no producer, so the
    800 ms budget reported `unavailable` for every real turn no matter how fast
    the system was.
    """
    recorder = LatencyRecorder()
    latency = TurnLatency("turn-1", into=recorder)
    latency.adopt_client_timings({"capture": 1500.0, "endpoint": 200.0})
    for stage in SPEECH_TO_FIRST_AUDIO:
        if stage != "endpoint":
            latency.complete(stage)
    latency.finish()
    report = recorder.report()
    assert report.speech_to_first_audio_median_ms is not None
    assert report.median_verdict != "unavailable"


def test_without_client_timings_the_figure_stays_unavailable():
    """The honest state before this slice, preserved as the fallback."""
    recorder = LatencyRecorder()
    latency = TurnLatency("turn-1", into=recorder)
    for stage in ("asr", "retrieve", "plan", "first_token", "tts_request", "first_audio"):
        latency.complete(stage)
    latency.finish()
    report = recorder.report()
    assert report.speech_to_first_audio_median_ms is None
    assert report.median_verdict == "unavailable"


def test_adopting_a_malformed_timing_never_breaks_the_turn():
    """D-025: a measurement that raises turns a slow turn into no reply."""
    recorder = LatencyRecorder()
    latency = TurnLatency("turn-1", into=recorder)
    latency.adopt_client_timings({"capture": float("nan"), "bogus": 5.0})
    latency.complete("asr")
    latency.finish()
    assert recorder.report().turns == 1
