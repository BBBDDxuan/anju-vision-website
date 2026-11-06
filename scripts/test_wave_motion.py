"""Numerical counterexamples for the offline demo's action heuristic."""
import math
import unittest
from wave_motion import analyze_motion


def recording(seconds=4, motion=lambda t: 0, translation=lambda t: 0, raised=True, identity=lambda t: 'p1'):
    frames = []
    for i in range(round(seconds * 30)):
        t = i / 30
        dx = translation(t)
        k = [[0, 0, 0] for _ in range(17)]
        k[5], k[6] = [100 + dx, 200, .98], [200 + dx, 200, .98]
        k[7], k[9] = [90 + dx, 150, .97], [100 + dx + motion(t) * 100, 80 if raised else 300, .96]
        k[8], k[10] = [210 + dx, 240, .98], [210 + dx, 300, .97]
        frames.append({'timeMs': t * 1000, 'validUntilMs': (i + 1) / 30 * 1000,
                       'people': [{'id': identity(t), 'keypoints': k}]})
    return frames


class WaveMotionTests(unittest.TestCase):
    def test_single_raised_wrist_reciprocation(self):
        events = analyze_motion(recording(motion=lambda t: .7 * math.sin(t * math.tau / .65)))
        self.assertEqual(len(events), 1)
        self.assertEqual([s['side'] for s in events[0]['sideIntervals']], ['left'])
        self.assertLess(events[0]['startMs'], events[0]['confirmedAtMs'])
        self.assertFalse(events[0]['helpIntentEstablished'])

    def test_static_raised_hands_translation_jitter_and_single_sweep(self):
        for kwargs in [{}, {'translation': lambda t: 80 * t},
                       {'motion': lambda t: .05 * math.sin(t * 100)},
                       {'motion': lambda t: t * .4}]:
            self.assertEqual(analyze_motion(recording(**kwargs)), [], kwargs)

    def test_walking_arm_swing_is_not_raised(self):
        self.assertEqual(analyze_motion(recording(motion=lambda t: math.sin(t * 10), raised=False)), [])

    def test_tracks_never_share_motion_history(self):
        self.assertEqual(analyze_motion(recording(motion=lambda t: math.sin(t * 12), identity=lambda t: f'p{int(t / .4)}')), [])

    def test_missing_and_uncertain_joints_break_continuity(self):
        frames = recording(motion=lambda t: math.sin(t * 12))
        for i, f in enumerate(frames):
            if i % 12 == 0:
                f['people'][0]['keypoints'][9][2] = .2
        self.assertEqual(analyze_motion(frames), [])
        short = recording(seconds=.5, motion=lambda t: math.sin(t * 12))
        later = recording(seconds=.5, motion=lambda t: math.sin(t * 12))
        for f in later:
            f['timeMs'] += 1000
            f['validUntilMs'] += 1000
        self.assertEqual(analyze_motion(short + later), [])

    def test_static_tail_does_not_extend_action(self):
        events = analyze_motion(recording(seconds=5, motion=lambda t: .7 * math.sin(min(t, 2) * math.tau / .65)))
        self.assertTrue(events)
        self.assertLess(events[0]['endMs'], 2200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
