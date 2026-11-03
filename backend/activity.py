"""Local pixel-change screening, never semantic evidence of presence or safety."""
import base64
from pathlib import Path

import cv2
import numpy as np


FILTER_VERSION = 'pixel-change-v1'
RISK_ACTIONS = {'fall', 'lying_on_floor', 'help_gesture', 'smoke', 'flame', 'sparks'}


class ActivityGate:
    def __init__(self, settings, saved=None):
        self.settings = settings
        saved = saved or {}
        self.previous = self._decode(saved.get('previous'))
        self.anchor = self._decode(saved.get('anchor'))
        self.last_model_ms = saved.get('last_model_ms')
        self.last_screen_end_ms = saved.get('last_screen_end_ms', -1)
        self.tail_until_ms = saved.get('tail_until_ms', -1)
        self.person_present = saved.get('person_present', False)
        self.risk_hold = saved.get('risk_hold', False)
        self.context_end_ms = saved.get('context_end_ms', -1)
        self.absence_watch_until_ms = saved.get('absence_watch_until_ms', -1)

    @staticmethod
    def signature(settings):
        return [FILTER_VERSION, settings.activity_filter_fps, settings.activity_filter_pixel_threshold,
                settings.activity_filter_change_ratio, settings.activity_filter_review_seconds,
                settings.activity_filter_person_review_seconds, settings.activity_filter_postroll_seconds, settings.absence_alert_seconds]

    @staticmethod
    def _decode(value):
        if not value:
            return None
        image = cv2.imdecode(np.frombuffer(base64.b64decode(value), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or max(image.shape[:2]) > 320:
            raise ValueError('Invalid activity checkpoint image')
        return image

    @staticmethod
    def _encode(image):
        if image is None:
            return None
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise ValueError('Activity checkpoint encoding failed')
        return base64.b64encode(encoded).decode('ascii')

    def state(self):
        return {**{key: getattr(self, key) for key in ['last_model_ms', 'last_screen_end_ms', 'tail_until_ms',
                                                    'person_present', 'risk_hold', 'context_end_ms', 'absence_watch_until_ms']},
                'previous': self._encode(self.previous), 'anchor': self._encode(self.anchor)}

    def _ratio(self, left, right):
        if left is None:
            return 0.0
        if left.shape != right.shape:
            return 1.0
        delta = cv2.absdiff(left, right).max(axis=2)
        return float(np.mean(delta >= self.settings.activity_filter_pixel_threshold))

    def evaluate(self, frames, start_ms, end_ms):
        """Compare neighbors and the last modeled scene to catch gradual/persistent changes."""
        maximum, first = 0.0, None
        ordered = sorted(frames, key=lambda f: f['pts_ms'])
        if len({f['pts_ms'] for f in ordered}) < 2:
            return {'skip': False, 'reason': 'insufficient_samples', 'max_change_ratio': None}
        for frame in ordered:
            try:
                image = cv2.imdecode(np.frombuffer(Path(frame['path']).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            except cv2.error as exc:
                raise ValueError('Invalid screening image') from exc
            if image is None:
                raise ValueError('Cannot read screening frame')
            scale = min(1, 320/max(image.shape[:2]))
            image = cv2.resize(image, (max(1,round(image.shape[1]*scale)), max(1,round(image.shape[0]*scale))))
            image = cv2.GaussianBlur(image, (5,5), 0)
            maximum = max(maximum, self._ratio(self.previous,image), self._ratio(self.anchor,image), self._ratio(first,image))
            if first is None:
                first = image
            self.previous = image
        self.last_screen_end_ms = end_ms
        changed = maximum >= self.settings.activity_filter_change_ratio
        if changed:
            self.tail_until_ms = max(self.tail_until_ms, end_ms+self.settings.activity_filter_postroll_seconds*1000)
        interval = (self.settings.activity_filter_person_review_seconds if self.person_present
                    else self.settings.activity_filter_review_seconds)*1000
        if self.last_model_ms is None:
            reason = 'initial_review'
        elif self.risk_hold:
            reason = 'risk_followup'
        elif start_ms < self.absence_watch_until_ms:
            reason = 'absence_followup'
        elif changed:
            reason = 'frame_change'
        elif start_ms < self.tail_until_ms:
            reason = 'activity_followup'
        elif start_ms-self.last_model_ms >= interval:
            reason = 'person_periodic_review' if self.person_present else 'periodic_review'
        else:
            reason = 'static_screened'
        return {'skip': reason == 'static_screened', 'reason': reason, 'max_change_ratio': round(maximum,6)}

    def analyzed(self, start_ms, end_ms, events, entities):
        if self.last_model_ms is None or start_ms >= self.last_model_ms:
            self.last_model_ms = start_ms
            if self.last_screen_end_ms == end_ms:
                self.anchor = self.previous.copy() if self.previous is not None else None
        for event in sorted(events, key=lambda e: (e['end_ms'], e['action'] != 'no_person_visible')):
            score = event.get('model_score')
            if event['epistemic_type'] != 'unknown' and (score is None or score >= .55) and event['action'] in RISK_ACTIONS:
                self.risk_hold = True
            if event['end_ms'] < self.context_end_ms or event['epistemic_type'] != 'observed':
                continue
            if entities.get(event['subject_id'], {}).get('entity_type') == 'person' and event['action'] not in {'unknown','occluded'}:
                self.person_present = True
                self.absence_watch_until_ms = -1
                self.context_end_ms = event['end_ms']
            elif (event['action'] == 'no_person_visible' and entities.get(event['subject_id'], {}).get('entity_type') == 'location'
                  and score is not None and score >= .75
                  and not event['uncertainty_reasons']):
                if self.person_present:
                    # Keep continuous semantic coverage while checking a possible absence.
                    self.absence_watch_until_ms = event['end_ms']+self.settings.absence_alert_seconds*1000
                self.person_present = False
                self.context_end_ms = event['end_ms']
