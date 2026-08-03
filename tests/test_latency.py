"""Part 7 slices 7.1-7.2: conversational latency instrumentation and wiring."""

import pytest

from src.open_llm_vtuber.latency import (
    BUDGET_SPEECH_TO_FIRST_AUDIO_MEDIAN_MS,
    SPEECH_TO_FIRST_AUDIO,
    STAGES,
    LatencyError,
    LatencyRecorder,
    StageSample,
    TurnLatency,
    TurnTimer,
    record_interruption_ms,
    recorder,
    reset_recorder,
)


class FakeClock:
    """A monotonic clock the test drives by hand, in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000.0


def _turn(clock: FakeClock, turn_id: str, **stage_ms: float) -> TurnTimer:
    timer = TurnTimer(turn_id, _clock=clock)
    for stage, milliseconds in stage_ms.items():
        clock.advance_ms(milliseconds)
        timer.complete(stage)
    return timer


def _full_turn(clock: FakeClock, turn_id: str, each_ms: float) -> TurnTimer:
    return _turn(clock, turn_id, **{stage: each_ms for stage in SPEECH_TO_FIRST_AUDIO})


def test_a_turn_reports_each_stage_separately():
    """One end-to-end number cannot be acted on; the stage breakdown can."""
    clock = FakeClock()
    timer = _turn(clock, "t1", endpoint=100.0, asr=250.0, plan=40.0)
    measured = {s.stage: s.milliseconds for s in timer.samples()}
    assert measured == pytest.approx({"endpoint": 100.0, "asr": 250.0, "plan": 40.0})


def test_stages_are_reported_in_pipeline_order_not_completion_order():
    clock = FakeClock()
    timer = TurnTimer("t1", _clock=clock)
    for stage in ("first_audio", "asr", "endpoint"):
        clock.advance_ms(10.0)
        timer.complete(stage)
    assert [s.stage for s in timer.samples()] == ["endpoint", "asr", "first_audio"]


def test_an_incomplete_turn_has_no_end_to_end_figure():
    """An interrupted turn is not a fast turn; recording zero would say it was."""
    clock = FakeClock()
    timer = _turn(clock, "t1", endpoint=50.0, asr=50.0)
    assert timer.speech_to_first_audio_ms() is None

    report = LatencyRecorder()
    report.record(timer)
    result = report.report()
    assert result.turns == 1
    assert result.complete_turns == 0
    assert result.speech_to_first_audio_median_ms is None
    assert result.median_verdict == "unavailable"


def test_a_complete_turn_sums_only_the_budgeted_stages():
    clock = FakeClock()
    timer = TurnTimer("t1", _clock=clock)
    clock.advance_ms(500.0)
    timer.complete("capture")  # before the budget boundary
    for stage in SPEECH_TO_FIRST_AUDIO:
        clock.advance_ms(10.0)
        timer.complete(stage)
    assert timer.speech_to_first_audio_ms() == pytest.approx(70.0)


def test_the_budget_verdict_is_measured_never_assumed():
    clock = FakeClock()
    recorder = LatencyRecorder()
    for index in range(5):
        recorder.record(_full_turn(clock, f"fast{index}", 10.0))
    fast = recorder.report()
    assert fast.speech_to_first_audio_median_ms == pytest.approx(70.0)
    assert fast.median_verdict == "pass"

    slow = LatencyRecorder()
    for index in range(5):
        slow.record(_full_turn(clock, f"slow{index}", 200.0))
    result = slow.report()
    assert (
        result.speech_to_first_audio_median_ms > BUDGET_SPEECH_TO_FIRST_AUDIO_MEDIAN_MS
    )
    assert result.median_verdict == "fail"


def test_the_report_names_the_slowest_stage():
    """The only reason to instrument stages is to know which one to fix."""
    clock = FakeClock()
    recorder = LatencyRecorder()
    recorder.record(
        _turn(clock, "t1", endpoint=20.0, asr=900.0, plan=30.0, first_token=40.0)
    )
    assert recorder.report().slowest_stage().stage == "asr"


def test_percentiles_are_real_measurements_not_interpolations():
    clock = FakeClock()
    recorder = LatencyRecorder()
    for index, cost in enumerate((10.0, 20.0, 30.0, 40.0, 1000.0)):
        recorder.record(_turn(clock, f"t{index}", asr=cost))
    stage = next(s for s in recorder.report().stages if s.stage == "asr")
    assert stage.p95_ms == 1000.0
    assert stage.worst_ms == 1000.0
    assert stage.median_ms == 30.0


def test_a_retried_stage_is_one_slower_stage_not_two():
    clock = FakeClock()
    timer = TurnTimer("t1", _clock=clock)
    clock.advance_ms(100.0)
    timer.complete("asr")
    clock.advance_ms(400.0)
    timer.complete("asr")
    samples = [s for s in timer.samples() if s.stage == "asr"]
    assert len(samples) == 1
    assert samples[0].milliseconds == pytest.approx(400.0)


def test_undeclared_stages_are_refused():
    """A free-form stage name is somewhere a prompt fragment ends up."""
    clock = FakeClock()
    timer = TurnTimer("t1", _clock=clock)
    with pytest.raises(LatencyError, match="undeclared"):
        timer.complete("thinking about the user's divorce")
    with pytest.raises(LatencyError, match="undeclared"):
        StageSample("t1", "arbitrary", 1.0)


def test_a_clock_that_steps_backwards_cannot_produce_negative_latency():
    """Wall clocks jump on suspend and NTP correction; percentiles must survive."""
    clock = FakeClock()
    timer = TurnTimer("t1", _clock=clock)
    clock.now -= 5.0
    assert timer.complete("asr") == 0.0
    with pytest.raises(LatencyError, match="negative"):
        StageSample("t1", "asr", -1.0)


def test_metric_events_carry_no_content():
    """These traces get pasted into issues; they must be safe to share."""
    clock = FakeClock()
    recorder = LatencyRecorder()
    recorder.record(_full_turn(clock, "t1", 12.0))
    events = list(recorder.metric_events())
    assert events, "instrumentation must emit something"
    for event in events:
        assert set(event).issubset(
            {"metric_id", "count", "median", "p95", "median_verdict", "p95_verdict"}
        )
        for value in event.values():
            assert (
                isinstance(value, (int, float))
                or value
                in {
                    "unavailable",
                    "pass",
                    "fail",
                }
                or str(value).startswith(("stage_latency_", "speech_to_first_audio"))
            )


def test_an_empty_session_reports_unavailable_rather_than_zero():
    result = LatencyRecorder().report()
    assert result.turns == 0
    assert result.stages == ()
    assert result.speech_to_first_audio_p95_ms is None
    assert result.p95_verdict == "unavailable"
    assert result.slowest_stage() is None


def test_interruption_is_measured_alongside_the_pipeline():
    recorder = LatencyRecorder()
    recorder.record_sample(StageSample("t1", "interruption", 180.0))
    stage = next(s for s in recorder.report().stages if s.stage == "interruption")
    assert stage.median_ms == 180.0


# --- slice 7.2: bounded retention and the live edge -------------------------


def test_a_long_session_does_not_grow_without_bound():
    """An eight-hour soak is a Part 7 gate; the recorder must not be the leak."""
    recorder_ = LatencyRecorder(retain=64)
    clock = FakeClock()
    for index in range(5000):
        recorder_.record(_turn(clock, f"t{index}", asr=float(index % 40)))
    stage = next(s for s in recorder_.report().stages if s.stage == "asr")
    assert stage.count == 64, "samples must be capped"
    # The lifetime turn count is a counter, so it stays exact.
    assert recorder_.report().turns == 5000


def test_a_recorder_must_retain_something():
    with pytest.raises(LatencyError):
        LatencyRecorder(retain=0)


def test_the_live_edge_never_raises_into_a_conversation():
    """A measurement failure must cost a sample, never a reply."""
    reset_recorder()
    latency = TurnLatency("live-turn")
    # Undeclared stages, repeated finishes, and use after finish are all bugs;
    # none of them may propagate out of the conversation path.
    latency.complete("not a real stage")
    latency.complete("asr")
    latency.finish()
    latency.complete("asr")
    latency.finish()
    assert recorder().report().turns == 1


def test_the_live_edge_records_what_it_did_measure():
    reset_recorder()
    latency = TurnLatency("live-turn")
    latency.complete("asr")
    latency.complete("plan")
    latency.finish()
    stages = {s.stage for s in recorder().report().stages}
    assert {"asr", "plan"} <= stages


def test_an_interrupted_turn_contributes_stages_but_no_end_to_end_figure():
    reset_recorder()
    latency = TurnLatency("barged-in")
    latency.complete("asr")
    latency.complete("plan")
    latency.finish()  # never reached audio
    report = recorder().report()
    assert report.turns == 1
    assert report.complete_turns == 0
    assert report.speech_to_first_audio_median_ms is None
    assert report.median_verdict == "unavailable"


def test_interruption_is_recorded_without_raising():
    reset_recorder()
    record_interruption_ms("turn-1", 120.0)
    record_interruption_ms("turn-2", float("nan"))  # must not explode
    record_interruption_ms("turn-3", float("inf"))
    stage = next(
        (s for s in recorder().report().stages if s.stage == "interruption"), None
    )
    assert stage is not None
    # NaN and infinity are refused rather than stored: one of either would
    # turn this stage's percentiles into NaN for the rest of the session.
    assert stage.count == 1
    assert stage.median_ms == 120.0


def test_the_pipeline_vocabulary_matches_what_the_conversation_path_records():
    """The wired stages must exist in the closed vocabulary, or nothing lands."""
    wired = {"asr", "plan", "first_token", "tts_request", "first_audio"}
    assert wired <= set(STAGES)
    assert wired <= set(SPEECH_TO_FIRST_AUDIO)


def test_there_is_no_enter_method():
    """`enter()` let a stage's time be filed under a different stage."""
    assert not hasattr(TurnTimer("t"), "enter")
