# IR Noise and Break Autocalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an IR autocalibration pipeline that rejects OFF-state noise, measures optical margin, verifies burst stability and simulated beam release, and exposes the diagnostics on `/config` without replacing the last valid calibration when an attempt fails.

**Architecture:** Keep hardware ownership in `app/ir_calibration.py`, but separate sampling analysis, candidate classification, timeout calculation, and ranking into pure functions covered by standard-library unit tests. Run the operational burst envelope in one dedicated daemon thread, pass the current runtime burst settings from `Chronometer`, and extend the existing response additively so saved JSON and the frontend remain backward compatible.

**Tech Stack:** Python 3, `unittest`, RPi.GPIO-compatible interface, kernel sysfs PWM/pigpio/RPi.GPIO backends, FastAPI, Vue 3, TypeScript, Vuetify.

## Global Constraints

- The first OFF pass represents time windows, not measured noise frequencies.
- Confirm OFF-state noise only when the later-selected aligned level persists for at least `0.002` seconds.
- Require at least `25` percentage points of active-versus-OFF contrast.
- Test at most five finalists with duty ratios `1.0`, `0.7`, `0.4`, and `0.2`; keep the configured duty in the final recommendation.
- Use the configured burst envelope, currently `0.006` seconds ON and `0.014` seconds OFF.
- Simulate beam break with the emitter OFF for `0.250` seconds.
- Calculate timeout as `max(3 * burst_period, 2 * max_signal_gap + 0.005)` and reject values above `0.120` seconds.
- Treat `50000` Hz only as the final deterministic tie-breaker.
- Never apply, save, or replace the last valid calibration when no valid recommendation exists.
- Always clean up the PWM output and burst thread in `finally` paths.
- Keep `frequency_hz`, `duty_cycle`, `burst_on`, `burst_off`, `sensor_active_level`, and `sensor_signal_timeout` backward compatible.
- Automated emitter shutdown does not validate the real optical path; return `physical_break_validated=false`.

## Hardware Validation Amendment - 2026-07-11

The first Raspberry run completed 51 OFF windows and 51 active windows. It
found 22 candidates with sufficient contrast, but every one suppressed a
continuous carrier within 0.073 to 0.578 seconds. Rejecting a saturated `hold`
therefore produced zero finalists even though the runtime `6ms/14ms` barrier
remained aligned.

The implementation was corrected after this evidence:

- `active_scan` samples each frequency in the configured burst envelope;
- `margin_test` also samples every duty in that envelope;
- continuous `hold` suppression remains diagnostic and no longer rejects a
  candidate;
- shortlist ranking uses burst-scan contrast and signal percentage, not
  continuous-hold survival;
- saved recommendations without a positive timeout at or below `0.120` seconds
  are ignored at startup.

The original task text below is retained as implementation history; statements
that reject a saturated hold are superseded by this amendment.

---

### Task 1: Pure Sampling, Noise, Margin, and Timeout Metrics

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_ir_calibration.py`
- Modify: `app/ir_calibration.py:180-503`

**Interfaces:**
- Produces: `summarize_samples(GPIO, samples, interval, confirm_time=0.002) -> dict`
- Produces: `evaluate_frequency(GPIO, noise_stats, freq, active_stats, min_delta, noise_confirm_time) -> dict`
- Produces: `classify_frequency_results(GPIO, noise_results, active_results, min_delta, noise_confirm_time) -> tuple[list, list]`
- Produces: `margin_duty_values(duty) -> list[float]`
- Produces: `minimum_stable_duty(results) -> float | None`
- Produces: `calculate_signal_timeout(burst_on, burst_off, max_signal_gap, max_timeout=0.12) -> dict`

- [ ] **Step 1: Write failing tests for sample runs and OFF noise classification**

```python
import unittest

from app.ir_calibration import (
    calculate_signal_timeout,
    classify_frequency_results,
    margin_duty_values,
    minimum_stable_duty,
    summarize_samples,
)


class FakeGPIO:
    LOW = 0
    HIGH = 1


class CalibrationMetricsTest(unittest.TestCase):
    def test_summarize_samples_tracks_confirmed_runs(self):
        stats = summarize_samples(FakeGPIO, [0, 0, 1, 1, 1, 0], 0.001, 0.002)
        self.assertEqual(stats["max_high_run_s"], 0.003)
        self.assertEqual(stats["first_high_confirmed_at"], 0.002)

    def test_classification_rejects_confirmed_off_signal(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0, 1, 1, 1, 0], 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )
        self.assertEqual(sensitive, [])
        self.assertEqual(rejected[0]["reasons"], ["noise_detected_off"])

    def test_classification_keeps_clean_high_contrast_signal(self):
        noise = [{
            "window_index": 0,
            "candidate_frequency_hz": 50000,
            "stats": summarize_samples(FakeGPIO, [0] * 5, 0.001, 0.002),
        }]
        active = [(50000, summarize_samples(FakeGPIO, [1] * 5, 0.001, 0.002))]
        sensitive, rejected = classify_frequency_results(
            FakeGPIO, noise, active, 25.0, 0.002
        )
        self.assertEqual([item["freq"] for item in sensitive], [50000])
        self.assertEqual(rejected, [])
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_ir_calibration -v`

Expected: import failures for the new metric functions.

- [ ] **Step 3: Implement sample summarization and candidate classification**

```python
def summarize_samples(GPIO, samples, interval, confirm_time=0.002):
    samples = list(samples)
    high = sum(level == GPIO.HIGH for level in samples)
    low = len(samples) - high
    transitions = sum(left != right for left, right in zip(samples, samples[1:]))

    def run_metrics(level):
        longest = 0
        current = 0
        first_confirmed_at = None
        required = max(1, math.ceil(confirm_time / interval))
        for index, sample in enumerate(samples):
            current = current + 1 if sample == level else 0
            longest = max(longest, current)
            if current >= required and first_confirmed_at is None:
                first_confirmed_at = (index - current + 1) * interval
        return round(longest * interval, 6), (
            round(first_confirmed_at, 6) if first_confirmed_at is not None else None
        )

    max_high, first_high = run_metrics(GPIO.HIGH)
    max_low, first_low = run_metrics(GPIO.LOW)
    count = len(samples)
    return {
        "samples": count,
        "high": high,
        "low": low,
        "high_pct": round(high / count * 100, 3) if count else 0,
        "low_pct": round(low / count * 100, 3) if count else 0,
        "transitions": transitions,
        "first": samples[0] if samples else None,
        "last": samples[-1] if samples else None,
        "max_high_run_s": max_high,
        "max_low_run_s": max_low,
        "first_high_confirmed_at": first_high,
        "first_low_confirmed_at": first_low,
    }
```

`read_window()` must collect GPIO samples and return `summarize_samples(...)`. `evaluate_frequency()` must use the active signal level selected by `score_frequency()`, reject a confirmed run at that level in the OFF stats, and add `noise_stats`, `noise_signal_pct`, `noise_longest_run_s`, and `reasons`. It must enforce floors of 25 percentage points and 0.002 seconds even when callers request weaker thresholds. `classify_frequency_results()` must require equal list lengths, pair OFF and active windows by temporal list position, expose `noise_window_index` and `candidate_frequency_hz`, and return clean and rejected lists. The OFF candidate label is diagnostic context, not a measured noise frequency.

- [ ] **Step 4: Add failing tests for margin values and timeout safety**

```python
    def test_margin_ratios_preserve_requested_duty_order(self):
        self.assertEqual(margin_duty_values(50), [50.0, 35.0, 20.0, 10.0])

    def test_minimum_stable_duty_uses_lowest_valid_result(self):
        results = [
            {"duty": 50.0, "valid": True},
            {"duty": 35.0, "valid": True},
            {"duty": 20.0, "valid": False},
        ]
        self.assertEqual(minimum_stable_duty(results), 35.0)

    def test_stable_20ms_burst_recommends_60ms_timeout(self):
        result = calculate_signal_timeout(0.006, 0.014, 0.020)
        self.assertEqual(result["signal_timeout"], 0.06)
        self.assertTrue(result["valid"])

    def test_timeout_above_120ms_is_invalid(self):
        result = calculate_signal_timeout(0.006, 0.014, 0.070)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "signal_gap_too_large")
```

- [ ] **Step 5: Run the new tests and verify RED**

Run: `python3 -m unittest tests.test_ir_calibration -v`

Expected: failures because margin and timeout helpers do not exist.

- [ ] **Step 6: Implement margin and timeout helpers, then verify GREEN**

```python
def margin_duty_values(duty):
    values = [round(max(0.0, min(100.0, float(duty) * ratio)), 3) for ratio in (1.0, 0.7, 0.4, 0.2)]
    return list(dict.fromkeys(values))


def minimum_stable_duty(results):
    stable = [float(item["duty"]) for item in results if item.get("valid")]
    return min(stable, default=None)


def calculate_signal_timeout(burst_on, burst_off, max_signal_gap, max_timeout=0.12):
    period = float(burst_on) + float(burst_off)
    timeout = round(max(period * 3, float(max_signal_gap) * 2 + 0.005), 6)
    effective_max_timeout = min(float(max_timeout), 0.120)
    valid = timeout <= effective_max_timeout
    return {
        "signal_timeout": timeout,
        "valid": valid,
        "reason": None if valid else "signal_gap_too_large",
    }
```

Run: `python3 -m unittest tests.test_ir_calibration -v`

Expected: all Task 1 tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add app/ir_calibration.py tests/__init__.py tests/test_ir_calibration.py
git commit -m "test: define IR calibration signal metrics"
```

### Task 2: Margin Sweep and Operational Burst/Break Tests

**Files:**
- Modify: `app/ir_calibration.py:234-730`
- Modify: `tests/test_ir_calibration.py`

**Interfaces:**
- Consumes: Task 1 metric helpers.
- Produces: `Emitter.set_duty(duty) -> None`
- Produces: `BurstEnvelope(emitter, frequency, burst_on, burst_off)` with `start()` and `stop()`.
- Produces: `run_margin_test(GPIO, sensor_pin, emitter, scan, noise_stats, duration, interval, settle, recovery, min_delta, confirm_time, window_reader=read_window) -> dict`
- Produces: `run_operational_test(GPIO, sensor_pin, emitter, scan, burst_on, burst_off, active_duration, break_duration, reacquire_duration, interval, confirm_time, max_signal_timeout, settle=0.05, window_reader=read_window) -> dict`

- [ ] **Step 1: Write failing tests for margin sweep ranking and burst cleanup**

```python
class FakeEmitter:
    def __init__(self):
        self.duty = 50.0
        self.events = []

    def set_duty(self, duty):
        self.duty = duty
        self.events.append(("duty", duty))

    def set_frequency(self, frequency):
        self.events.append(("on", frequency, self.duty))

    def off(self):
        self.events.append(("off",))


class OperationalHelpersTest(unittest.TestCase):
    def test_burst_envelope_stops_with_emitter_off(self):
        emitter = FakeEmitter()
        envelope = BurstEnvelope(emitter, 50000, 0.001, 0.001)
        envelope.start()
        time.sleep(0.005)
        envelope.stop()
        self.assertFalse(envelope.is_alive())
        self.assertEqual(emitter.events[-1], ("off",))

    def test_margin_sweep_uses_lowest_stable_duty_and_restores_requested_duty(self):
        emitter = FakeEmitter()
        noise = summarize_samples(FakeGPIO, [0] * 10, 0.001, 0.002)
        scan = {"freq": 50000, "signal_level": FakeGPIO.HIGH}

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            samples = [1] * 10 if emitter.duty >= 35 else [0] * 10
            return summarize_samples(GPIO, samples, interval, confirm_time)

        result = run_margin_test(
            FakeGPIO, 17, emitter, scan, noise, 0.01, 0.001,
            0, 0, 25.0, 0.002, window_reader=reader,
        )

        self.assertEqual(result["minimum_stable_duty"], 35.0)
        self.assertEqual(emitter.duty, 50.0)

    def test_operational_test_detects_break_and_reacquisition(self):
        emitter = FakeEmitter()
        windows = iter([
            summarize_samples(FakeGPIO, [1] * 6 + [0] * 14 + [1] * 6, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 250, 0.001, 0.002),
            summarize_samples(FakeGPIO, [0] * 5 + [1] * 50, 0.001, 0.002),
        ])

        def reader(GPIO, sensor_pin, duration, interval, confirm_time):
            return next(windows)

        result = run_operational_test(
            FakeGPIO,
            17,
            emitter,
            {"freq": 50000, "signal_level": FakeGPIO.HIGH},
            0.006,
            0.014,
            0.026,
            0.250,
            0.055,
            0.001,
            0.002,
            0.120,
            settle=0,
            window_reader=reader,
        )

        self.assertTrue(result["break_detected"])
        self.assertEqual(result["signal_timeout"], 0.06)
        self.assertEqual(result["break_release_s"], 0.0)
        self.assertEqual(result["reacquire_s"], 0.005)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_ir_calibration -v`

Expected: import failures for `BurstEnvelope` and operational helpers.

- [ ] **Step 3: Implement duty updates and the dedicated envelope thread**

```python
class BurstEnvelope:
    def __init__(self, emitter, frequency, burst_on, burst_off):
        self.emitter = emitter
        self.frequency = int(frequency)
        self.burst_on = max(0.0005, float(burst_on))
        self.burst_off = max(0.0005, float(burst_off))
        self._stop = threading.Event()
        self._thread = None
        self._error = None
        self._carrier_lock = threading.Lock()

    def start(self):
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="agility-ir-calibration-burst", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                with self._carrier_lock:
                    if self._stop.is_set():
                        break
                    self.emitter.set_frequency(self.frequency)
                if self._stop.wait(self.burst_on):
                    break
                with self._carrier_lock:
                    self.emitter.off()
                if self._stop.wait(self.burst_off):
                    break
        except Exception as exc:
            self._error = exc
        finally:
            with self._carrier_lock:
                self.emitter.off()

    def stop(self):
        self._stop.set()
        with self._carrier_lock:
            self.emitter.off()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, (self.burst_on + self.burst_off) * 4))
        with self._carrier_lock:
            self.emitter.off()
        if self.is_alive():
            raise CalibrationError("thread do envelope de burst nao encerrou")
        error, self._error = self._error, None
        if error is not None:
            raise CalibrationError(f"falha na thread do envelope IR: {error}") from error

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())
```

`Emitter.set_duty()` must clamp to 0..100 and update the stored duty without leaving the carrier active. `run_margin_test()` must restore the requested duty in `finally`, reject a response whose detected signal level differs from the original scan level, and preserve the stable rejection reason `signal_level_changed`. Fault-injection tests must cover the real `Emitter.set_duty()`, reader failure, duty restoration, and exact duty order.

- [ ] **Step 4: Implement operational sampling and simulated break**

`run_operational_test()` must:

```python
envelope.start()
active = window_reader(GPIO, sensor_pin, active_duration, interval, confirm_time)
envelope.stop()
timeout = calculate_signal_timeout(burst_on, burst_off, active_max_gap, max_timeout)
broken = window_reader(GPIO, sensor_pin, break_duration, interval, confirm_time)
envelope.start()
reacquired = window_reader(GPIO, sensor_pin, reacquire_duration, interval, confirm_time)
envelope.stop()
```

It must derive the max signal gap from the longest run at the opposite level. The break window must use the calculated candidate timeout as its confirmation duration so `break_release_s` identifies the first break-level run long enough to trigger the logical break, rather than a short flicker. Reacquisition sampling must start immediately after restarting the envelope, without a settle delay that would hide latency. It must count residual signal samples and always stop the envelope in `finally`. Tests must prove worker exceptions propagate after PWM shutdown, that a second `stop()` does not re-raise a consumed worker error, that a delayed carrier activation cannot complete after `stop()` returns, that reacquisition starts before its reader, and that caller-supplied window durations and break confirmation timeout are preserved.

- [ ] **Step 5: Verify GREEN and commit Task 2**

Run: `python3 -m unittest tests.test_ir_calibration -v`

Expected: all Task 1 and Task 2 tests pass.

```bash
git add app/ir_calibration.py tests/test_ir_calibration.py
git commit -m "feat: measure IR margin and simulated break"
```

### Task 3: Integrate the Multi-Phase Calibration Pipeline

**Files:**
- Modify: `app/ir_calibration.py:458-824`
- Modify: `app/chrono.py:145-169,1191-1319`
- Modify: `rasp_scripts/testar_sensor_ir.py:22-168`
- Modify: `tests/test_ir_calibration.py`
- Create: `tests/test_chrono_calibration.py`

**Interfaces:**
- Consumes: Tasks 1 and 2 helpers.
- Produces: `choose_operational_candidate(candidates, preferred_frequency=None) -> dict | None`
- Produces: `calibration_result_is_valid(result) -> bool`
- Produces: additive result keys `noise_scan`, `rejected`, `margin`, `burst`, `break_tests`, and `diagnostics`.
- Produces: recommendation fields `minimum_stable_duty`, `burst_max_signal_gap`, `break_release_s`, `reacquire_s`, and `physical_break_validated`.
- Produces: calibration state field `last_attempt` while retaining `last_result` as the last valid result.

- [ ] **Step 1: Write failing pipeline tests**

Add tests around pure selection and persistence decisions:

```python
    def candidate_result(self, freq, minimum_stable_duty):
        return {
            "scan": {"freq": freq, "delta": 100.0, "signal_pct": 100.0},
            "margin": {"minimum_stable_duty": minimum_stable_duty},
            "burst": {"max_signal_gap": 0.020},
            "break_test": {
                "break_detected": True,
                "break_release_s": 0.0,
                "reacquire_s": 0.005,
            },
            "signal_timeout": 0.060,
        }

    def test_candidate_ranking_prefers_lower_stable_duty_before_frequency_preference(self):
        candidates = [
            self.candidate_result(50000, minimum_stable_duty=35.0),
            self.candidate_result(48000, minimum_stable_duty=20.0),
        ]
        selected = choose_operational_candidate(candidates, preferred_frequency=50000)
        self.assertEqual(selected["scan"]["freq"], 48000)

    def test_failed_attempt_is_not_persistable(self):
        self.assertFalse(calibration_result_is_valid({"ok": False, "recommendation": None}))
        self.assertTrue(calibration_result_is_valid({"ok": True, "recommendation": {"frequency_hz": 50000}}))
```

Add an actual `Chronometer._mark_calibration_finished()` regression in `tests/test_chrono_calibration.py` using an object created with `object.__new__(Chronometer)`, an `RLock`, and a no-op `_mark_state_changed_locked`. Start with an existing valid `last_result`, finish an invalid attempt, and assert `last_attempt` changes while both `last_result` and `_calibration_last_result` retain the existing valid object.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_ir_calibration -v`

Expected: failures for selection and validity helpers.

- [ ] **Step 3: Refactor `run_ir_calibration()` into the approved phases**

Add parameters with these defaults:

```python
noise_confirm_time=0.002,
finalist_count=5,
burst_on=0.006,
burst_off=0.014,
burst_test_duration=1.0,
break_duration=0.25,
reacquire_duration=0.5,
max_signal_timeout=0.12,
```

The flow must emit progress phases `noise_scan`, `active_scan`, `hold`, `margin_test`, `burst_test`, `break_test`, and `select`. Build the noise windows first with the emitter continuously OFF, run active windows second, classify candidates, reject saturated holds, test only the top five finalists, and produce all additive result arrays.

Use these exact data relationships:

- `noise_scan` entries are `{window_index, candidate_frequency_hz, stats}` and are paired with active scans by list position.
- Early rejected scans retain their diagnostics and use public reason codes `noise_detected_off` and `insufficient_contrast`.
- A saturated hold appends `continuous_signal_suppressed` and cannot become a finalist.
- Select up to five finalists by hold stability, contrast, and signal percentage; use distance from the preferred frequency only to resolve otherwise equivalent shortlist candidates.
- Each finalist record is `{scan, hold, margin, burst, break_test, signal_timeout, valid, reasons}`.
- `margin` is a list of `{freq, requested_duty, minimum_stable_duty, results}`.
- `burst` is a list of `{freq, max_signal_gap, stats}`.
- `break_tests` is a list containing frequency, break detection, release, residual samples, reacquisition, timeout, and timeout validity.
- Finalists with no stable margin use `insufficient_contrast`; invalid timeout uses `signal_gap_too_large`; failed simulated break uses `break_not_detected`.

`choose_operational_candidate()` must filter invalid candidates and order valid candidates by lower `minimum_stable_duty`, lower burst max gap, lower break release, lower reacquisition time, higher scan contrast, then proximity to the preferred frequency. Missing timing values sort after real values.

The recommendation must use the configured burst values and selected timeout, preserve all existing fields, and add `minimum_stable_duty`, `burst_max_signal_gap`, `break_release_s`, `reacquire_s`, and `physical_break_validated=False`.

Add one pipeline-level test with a fake emitter and deterministic readers/runners. It must prove all OFF windows are read before any active carrier event, only clean finalists reach margin/operational tests, all additive result keys exist, and a no-candidate result has `ok=False` without a recommendation.

Keep `baseline` as the selected candidate's OFF stats, or the first OFF stats when no candidate is selected. Keep the existing `scan`, `sensitive`, `hold`, and `recommendation` keys.

- [ ] **Step 4: Preserve the last valid calibration in `Chronometer`**

```python
def _mark_calibration_finished(self, result=None, error=None):
    valid = calibration_result_is_valid(result)
    # Always place a returned result in calibration.last_attempt.
    # Update last_result and _calibration_last_result only when valid is true.
```

Initialize `last_attempt` to `None`, clear it when a new calibration starts, and expose it in the calibration status. Pass the current runtime burst settings to `run_ir_calibration()`. Apply and save only when `calibration_result_is_valid(result)` is true. A failed attempt must return HTTP 200 diagnostics without mutating runtime, `last_result`, `_calibration_last_result`, or the saved file.

- [ ] **Step 5: Extend CLI arguments and terminal output**

Add CLI flags for noise confirmation, finalist count, burst times, break duration, and max timeout. Print OFF contamination, rejection reasons, margin duty, burst gap, release, reacquisition, and the physical-validation warning.

- [ ] **Step 6: Verify and commit Task 3**

Run:

```bash
python3 -m unittest tests.test_ir_calibration -v
python3 -m py_compile app/ir_calibration.py app/chrono.py app/main.py rasp_scripts/testar_sensor_ir.py
```

Expected: all tests pass and compilation exits 0.

```bash
git add app/ir_calibration.py app/chrono.py rasp_scripts/testar_sensor_ir.py tests/test_ir_calibration.py
git commit -m "feat: integrate noise-aware IR autocalibration"
```

### Task 4: Expose Calibration Diagnostics on `/config`

**Files:**
- Modify: `frontend-agility-admin/src/types/index.ts:148-237`
- Modify: `frontend-agility-admin/src/views/config/ConfigView.vue:1-431`

**Interfaces:**
- Consumes: additive backend result fields from Task 3.
- Produces: backward-compatible optional TypeScript fields and diagnostic tables.

- [ ] **Step 1: Add optional TypeScript result types before changing the template**

```typescript
export interface IrCalibrationRejectedFrequency {
    freq: number
    reasons: string[]
    signal_level_name?: string
    noise_signal_pct?: number
    delta?: number
}

export interface IrCalibrationOperationalResult {
    freq: number
    minimum_stable_duty?: number | null
    burst_max_signal_gap?: number | null
    break_release_s?: number | null
    reacquire_s?: number | null
    break_detected?: boolean
}
```

Make `noise_scan`, `rejected`, `margin`, `burst`, `break_tests`, `diagnostics`, and `calibration.last_attempt` optional so existing saved JSON still renders.

- [ ] **Step 2: Run type checking and verify the unchanged template still passes**

Run: `npm run type-check`

Expected: exit 0.

- [ ] **Step 3: Render latest-attempt diagnostics**

Use `calibration.last_attempt ?? calibration.last_result ?? saved_calibration`. Add computed values for contaminated window count, rejected rows, and finalist metrics. Show:

- a warning when `physical_break_validated === false`;
- rejected frequency and translated reason;
- noise percentage and active contrast;
- minimum stable duty;
- burst max gap, release, and reacquisition;
- recommended timeout.

Keep the existing calibration button and hardware status behavior unchanged.

- [ ] **Step 4: Verify and commit Task 4**

Run:

```bash
npm run type-check
npm run build
```

Expected: both commands exit 0.

Commit in the frontend repository:

```bash
git add src/types/index.ts src/views/config/ConfigView.vue
git commit -m "feat: show IR calibration diagnostics"
```

### Task 5: Documentation and End-to-End Verification

**Files:**
- Modify: `ir_implementation_doc.md:279-341`
- Modify: `readme.md:98-119`
- Modify: `README_2.md:155-167`

**Interfaces:**
- Consumes: final backend and frontend behavior.
- Produces: deployment and interpretation guidance for later TCC updates.

- [ ] **Step 1: Document the new phases and lessons**

Record that OFF windows are temporal noise samples, explain the duty-margin test, simulated break limitation, dynamic timeout, new result fields, and the physical validation procedure.

- [ ] **Step 2: Run full local verification**

Run in the backend:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app/ir_calibration.py app/chrono.py app/main.py rasp_scripts/testar_sensor_ir.py
git diff --check
```

Run in the frontend:

```bash
npm run type-check
npm run build
git diff --check
```

Expected: all commands exit 0 with no test failures.

- [ ] **Step 3: Commit backend documentation**

```bash
git add ir_implementation_doc.md readme.md README_2.md
git commit -m "docs: explain noise-aware IR calibration"
```

- [ ] **Step 4: Push both local repositories and update only the backend on Raspberry**

Push the current backend and frontend branches. In the existing Raspberry SSH terminal, run `git pull --ff-only` only in `~/Desktop/agility/fastAPI-agility`.

- [ ] **Step 5: Run Raspberry calibration and inspect the result**

Stop the backend process using the listener PID for port 8000, restart it with the existing hardware PWM environment, invoke `POST /config/ir/calibracao`, and verify:

- the IR LED remains OFF throughout `noise_scan`;
- `noise_scan`, `rejected`, `margin`, `burst`, and `break_tests` are returned;
- `emissor_modo` remains `kernel.sysfs.PWM.burst`;
- a valid recommendation uses a timeout no greater than `0.120` seconds;
- an invalid attempt does not overwrite the previous calibration file.

- [ ] **Step 6: Coordinate the physical passage test**

Ask the user to block the real optical path with an opaque object while monitoring `sensor_estado_feixe`, `sensor_quebras_logicas`, and accepted events. Record the physical result in `ir_implementation_doc.md` only after it is observed.
