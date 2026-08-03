"""Per-stage conversational latency, measured before anything is tuned.

Part 7 promises a median speech-to-first-audio under 800 ms and a p95 under
1.4 s. Nothing in the system could report either number, and a budget that
cannot be measured is a wish. Worse, a single end-to-end figure cannot be acted
on: 900 ms is a different engineering problem depending on whether it was spent
waiting for endpointing, for the first model token, or for the first TTS chunk.

So this records the turn as a sequence of stages and reports each one.

Three properties matter as much as the numbers:

**It is content-free.** A stage name, a monotonic duration, and a correlation
id. No transcript, no audio, no prompt, no reply. The same rule the study log
and the evidence envelope already follow (D-003, D-006), for the same reason:
latency traces are the ones people actually paste into issues.

**It uses a monotonic clock.** Wall-clock time on a laptop that suspends, or
crosses an NTP correction mid-turn, produces negative durations and impossible
percentiles. `time.perf_counter` cannot go backwards.

**A missing stage is missing, not zero.** A turn where TTS never started has no
TTS duration. Recording zero would silently pull every percentile down and
make the system look fastest exactly when it is most broken — the same failure
Part 2 refused when it made unmeasured metrics `unavailable` rather than 0.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterator

# The stages of one spoken turn, in the order they occur. Closed, because a
# free-form stage name is a place for a prompt fragment to end up.
STAGES: tuple[str, ...] = (
    "capture",  # microphone open to captured utterance
    "endpoint",  # utterance end to end-of-speech decision
    "asr",  # audio handed to ASR to final transcript
    "retrieve",  # memory and evidence context assembled
    "plan",  # request queued to model accepted it
    "first_token",  # model accepted to first token out
    "tts_request",  # first sentence ready to TTS accepted it
    "first_audio",  # TTS accepted to first audio chunk playable
)

# The stage boundary the product budget is stated against: the user stops
# speaking and the companion starts making sound.
SPEECH_TO_FIRST_AUDIO = (
    "endpoint",
    "asr",
    "retrieve",
    "plan",
    "first_token",
    "tts_request",
    "first_audio",
)

# Frozen Part 7 budgets, in milliseconds. Targets, not claims: a number here is
# what the release gate demands, never what the system has been shown to do.
BUDGET_SPEECH_TO_FIRST_AUDIO_MEDIAN_MS = 800.0
BUDGET_SPEECH_TO_FIRST_AUDIO_P95_MS = 1400.0
BUDGET_INTERRUPTION_MS = 250.0


class LatencyError(ValueError):
    """A latency measurement was recorded incoherently."""


@dataclass(frozen=True)
class StageSample:
    """One stage of one turn."""

    turn_id: str
    stage: str
    milliseconds: float

    def __post_init__(self) -> None:
        if self.stage not in STAGES and self.stage != "interruption":
            raise LatencyError(f"undeclared latency stage: {self.stage}")
        # NaN fails every comparison, so a bare `< 0` check waves it through
        # and one poisoned sample turns every percentile that stage will ever
        # report into NaN. Infinity is refused for the same reason.
        if not math.isfinite(self.milliseconds):
            raise LatencyError("a stage duration must be a finite number")
        if self.milliseconds < 0:
            raise LatencyError("a stage cannot take negative time")


@dataclass
class TurnTimer:
    """Times one turn's stages against a monotonic clock.

    Stages are marked as they complete. A stage that never completes is simply
    absent from the result, which is what lets the report distinguish "TTS took
    no time" from "TTS never happened".
    """

    turn_id: str
    _clock: Callable[[], float] = field(default=time.perf_counter, repr=False)
    _mark: float = field(default=0.0, repr=False)
    _stages: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._mark = self._clock()

    def complete(self, stage: str) -> float:
        """Close ``stage`` and return how long it took, in milliseconds.

        There is deliberately no `enter()`. One cursor advances through the
        pipeline, so a stage is "the time since the previous stage closed";
        an explicit start would suggest stages can be timed independently and
        out of order, which for a sequential turn they cannot — the earlier
        API let `enter("asr")` followed by `complete("plan")` file ASR's time
        under planning without complaining.
        """
        if stage not in STAGES and stage != "interruption":
            raise LatencyError(f"undeclared latency stage: {stage}")
        now = self._clock()
        elapsed = max(0.0, (now - self._mark) * 1000.0)
        # Re-entering the same stage replaces rather than accumulates: a retried
        # ASR pass is one ASR stage that took longer, not two.
        self._stages[stage] = elapsed
        self._mark = now
        return elapsed

    def samples(self) -> tuple[StageSample, ...]:
        return tuple(
            StageSample(self.turn_id, stage, self._stages[stage])
            for stage in (*STAGES, "interruption")
            if stage in self._stages
        )

    def speech_to_first_audio_ms(self) -> float | None:
        """The product figure, or None when the turn never reached audio.

        A partial turn returns None rather than a sum of the stages it did
        reach, because a turn that was interrupted before speaking is not a
        fast turn.
        """
        if any(stage not in self._stages for stage in SPEECH_TO_FIRST_AUDIO):
            return None
        return sum(self._stages[stage] for stage in SPEECH_TO_FIRST_AUDIO)


@dataclass(frozen=True)
class StageReport:
    """What one stage cost across a session."""

    stage: str
    count: int
    median_ms: float
    p95_ms: float
    worst_ms: float


@dataclass(frozen=True)
class LatencyReport:
    """Per-stage and end-to-end latency for a session, with budget verdicts."""

    stages: tuple[StageReport, ...]
    turns: int
    complete_turns: int
    speech_to_first_audio_median_ms: float | None
    speech_to_first_audio_p95_ms: float | None
    median_verdict: str
    p95_verdict: str

    def slowest_stage(self) -> StageReport | None:
        """Where the time actually goes — the only reason to instrument stages."""
        return max(self.stages, key=lambda s: s.median_ms, default=None)


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile.

    Deliberately not interpolated: with the handful of turns a pilot session
    produces, interpolation invents a value between two real measurements and
    reports it to three decimal places. The nearest rank is always a latency
    that actually occurred.
    """
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


def _verdict(value: float | None, budget: float) -> str:
    if value is None:
        return "unavailable"
    return "pass" if value <= budget else "fail"


# How many samples per stage a recorder keeps. An eight-hour soak is an
# explicit Part 7 gate, and a recorder that appended one row per stage per turn
# forever would be the resource leak that gate exists to catch. A ring buffer
# keeps percentiles describing recent behaviour, which is what an operator
# console is for; the lifetime turn count is still exact because it is a
# counter rather than a list.
DEFAULT_RETAINED_SAMPLES = 2048


class LatencyRecorder:
    """Collects stage samples across a session and reports them.

    Thread-safe because audio capture, the model stream, and TTS complete on
    different threads, and a partially written sample list would corrupt a
    percentile rather than raise.
    """

    def __init__(self, *, retain: int = DEFAULT_RETAINED_SAMPLES) -> None:
        if retain < 1:
            raise LatencyError("a recorder must retain at least one sample")
        self._lock = threading.Lock()
        self._retain = retain
        self._by_stage: dict[str, deque[float]] = {}
        self._end_to_end: deque[float] = deque(maxlen=retain)
        # Counted, not collected: turn ids are unbounded and only their number
        # is ever reported.
        self._turns = 0
        self._seen_turn: str | None = None

    def timer(self, turn_id: str) -> TurnTimer:
        return TurnTimer(turn_id)

    def _count_turn(self, turn_id: str) -> None:
        if turn_id != self._seen_turn:
            self._seen_turn = turn_id
            self._turns += 1

    def record(self, timer: TurnTimer) -> None:
        """Take everything one finished turn measured."""
        with self._lock:
            self._count_turn(timer.turn_id)
            for sample in timer.samples():
                self._by_stage.setdefault(
                    sample.stage, deque(maxlen=self._retain)
                ).append(sample.milliseconds)
            end_to_end = timer.speech_to_first_audio_ms()
            if end_to_end is not None:
                self._end_to_end.append(end_to_end)

    def record_sample(self, sample: StageSample) -> None:
        with self._lock:
            self._count_turn(sample.turn_id)
            self._by_stage.setdefault(sample.stage, deque(maxlen=self._retain)).append(
                sample.milliseconds
            )

    def report(self) -> LatencyReport:
        with self._lock:
            by_stage = {stage: list(values) for stage, values in self._by_stage.items()}
            end_to_end = list(self._end_to_end)
            turns = self._turns

        stages = tuple(
            StageReport(
                stage=stage,
                count=len(by_stage[stage]),
                median_ms=round(statistics.median(by_stage[stage]), 3),
                p95_ms=round(_percentile(by_stage[stage], 0.95), 3),
                worst_ms=round(max(by_stage[stage]), 3),
            )
            for stage in (*STAGES, "interruption")
            if stage in by_stage
        )

        median = round(statistics.median(end_to_end), 3) if end_to_end else None
        p95 = round(_percentile(end_to_end, 0.95), 3) if end_to_end else None
        return LatencyReport(
            stages=stages,
            turns=turns,
            complete_turns=len(end_to_end),
            speech_to_first_audio_median_ms=median,
            speech_to_first_audio_p95_ms=p95,
            median_verdict=_verdict(median, BUDGET_SPEECH_TO_FIRST_AUDIO_MEDIAN_MS),
            p95_verdict=_verdict(p95, BUDGET_SPEECH_TO_FIRST_AUDIO_P95_MS),
        )

    def metric_events(self) -> Iterator[dict[str, float | str | int]]:
        """Content-free payloads for the Part 2 evidence envelope.

        Only stage names, counts, and rounded milliseconds, so these can be
        shared under the same privacy rules as every other trace.
        """
        report = self.report()
        for stage in report.stages:
            yield {
                "metric_id": f"stage_latency_{stage.stage}_ms",
                "count": stage.count,
                "median": stage.median_ms,
                "p95": stage.p95_ms,
            }
        yield {
            "metric_id": "speech_to_first_audio_ms",
            "count": report.complete_turns,
            "median": report.speech_to_first_audio_median_ms
            if report.speech_to_first_audio_median_ms is not None
            else "unavailable",
            "p95": report.speech_to_first_audio_p95_ms
            if report.speech_to_first_audio_p95_ms is not None
            else "unavailable",
            "median_verdict": report.median_verdict,
            "p95_verdict": report.p95_verdict,
        }


# --- live integration ---------------------------------------------------------

# One recorder per process. The conversation path is the only writer and the
# operator console the only reader, so a module-level instance avoids threading
# a recorder through every call site that might want to time something.
_recorder = LatencyRecorder()


def recorder() -> LatencyRecorder:
    return _recorder


def reset_recorder() -> None:
    """Drop collected latency. Used by the operator console and by tests."""
    global _recorder
    _recorder = LatencyRecorder()


class TurnLatency:
    """A turn timer that cannot break the turn it is measuring.

    The core API above is strict on purpose: an undeclared stage or a negative
    duration is a bug and should fail loudly in tests. On the live
    conversation path the calculus inverts — a measurement that raises would
    turn "we cannot time this turn" into "the user gets no reply." So every
    method here swallows, and the cost of a mistake is a missing sample.

    This is the same separation the codebase already makes for provenance:
    memory evidence must not make a live conversation unavailable.
    """

    def __init__(self, turn_id: str, *, into: LatencyRecorder | None = None) -> None:
        self._into = into if into is not None else _recorder
        try:
            self._timer: TurnTimer | None = TurnTimer(turn_id)
        except Exception:
            self._timer = None

    def complete(self, stage: str) -> None:
        if self._timer is None:
            return
        try:
            self._timer.complete(stage)
        except Exception:
            pass

    def finish(self) -> None:
        if self._timer is None:
            return
        try:
            self._into.record(self._timer)
        except Exception:
            pass
        finally:
            self._timer = None


def record_interruption_ms(turn_id: str, milliseconds: float) -> None:
    """Record how long a barge-in took to stop stale output, never raising."""
    try:
        _recorder.record_sample(StageSample(turn_id, "interruption", milliseconds))
    except Exception:
        pass
