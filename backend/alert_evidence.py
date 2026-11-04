"""Choose a display frame by event phase, independently of review frame order.

The selected JPEG is local context for an already confirmed candidate. Its time
is not a second visual verdict and must not change the incident's time range.
"""
import math

POLICY = 'event-phase-v1'


def select_frame(candidate, reviewed_ids=()):
    frames = [f for f in candidate.get('frames', []) if isinstance(f, dict)]
    timed = [f for f in frames if type(f.get('pts_ms')) is int and f['pts_ms'] >= 0]
    t = candidate.get('t')
    if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and t >= 0:
        trigger = round(t * 1000)
        if candidate.get('event_type') == 'suspected_fall':
            # The collector retains one second after detection. Prefer +0.5s,
            # rather than the descent/pre-roll or a much later unrelated scene.
            post = [f for f in timed if trigger < f['pts_ms'] <= trigger + 1000]
            if post:
                return min(post, key=lambda f: (abs(f['pts_ms'] - trigger - 500), -f['pts_ms'])), 'post_event'
        # Help/flame have a hold gate: use the closest sampled frame at/before
        # the trigger, so an extinguished flame/lowered arms are not preferred.
        before = [f for f in timed if trigger - 350 <= f['pts_ms'] <= trigger]
        after = [f for f in timed if trigger < f['pts_ms'] <= trigger + 350]
        frame = (max(before, key=lambda f: f['pts_ms']) if before else
                 min(after, key=lambda f: f['pts_ms']) if after else None)
        phase = 'trigger_fallback' if candidate.get('event_type') == 'suspected_fall' else 'during_event'
        return frame, phase if frame else 'unavailable'
    # Legacy records lack a trustworthy trigger. Never label an arbitrary frame
    # as post-fall/active-event evidence. Retain the latest reviewed image only.
    reviewed = [f for f in frames if f.get('id') in set(reviewed_ids)]
    frame = max(reviewed, key=lambda f: f.get('pts_ms', -1)) if reviewed else None
    return frame, 'timing_unavailable' if frame else 'unavailable'
