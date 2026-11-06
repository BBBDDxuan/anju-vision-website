"""Avoid turning missing scans into negatives or dropping non-confirmations."""
import pytest

from scripts.evaluate_full_fall_dataset import metrics


def test_video_confusion_matrix_includes_nonconfirmed_positive_videos():
    rows = [dict(truth_fall=True,scan_complete=True,confirmed=True),
            dict(truth_fall=True,scan_complete=True,confirmed=False),
            dict(truth_fall=False,scan_complete=True,confirmed=True),
            dict(truth_fall=False,scan_complete=True,confirmed=False)]
    result=metrics(rows,'confirmed')
    assert result==dict(evaluated=4,missing=0,tp=1,fn=1,fp=1,tn=1,
                        recall=.5,specificity=.5,precision=.5,accuracy=.5,f1=.5)


def test_partial_or_unreviewed_videos_are_explicitly_missing():
    rows=[dict(truth_fall=True,scan_complete=False,confirmed=False),
          dict(truth_fall=True,scan_complete=True,confirmed=None),
          dict(truth_fall=False,scan_complete=True,confirmed=False)]
    result=metrics(rows,'confirmed')
    assert result['evaluated']==1 and result['missing']==2 and result['tn']==1
    assert result['fn']==0 and result['recall'] is None and result['precision'] is None


def test_no_candidates_keeps_negative_video_in_denominator():
    rows=[dict(truth_fall=False,scan_complete=True,local_candidate=False),
          dict(truth_fall=True,scan_complete=True,local_candidate=True)]
    result=metrics(rows,'local_candidate')
    assert result['accuracy']==1 and result['recall']==1 and result['specificity']==1
