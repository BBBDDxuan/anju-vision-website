from types import SimpleNamespace
import numpy as np
import pytest

from ai_service.config import Config, ModelConfig, update
from ai_service.flame_tiles import flame_views, merge_flames, starts
from ai_service.models import LocalModels
from ai_service.types import Flame


def test_views_cover_frame_with_overlap_and_bounded_budget():
    image = np.zeros((1620, 2880, 3), np.uint8)
    views = flame_views(image, 1280, .25, 12)
    coverage = np.zeros(image.shape[:2], np.uint8)
    assert 1 < len(views) <= 12
    assert any(x > 0 and y > 0 for _, x, y in views)
    for view, x, y in views:
        h, w = view.shape[:2]
        coverage[y:y+h, x:x+w] += 1
        assert np.shares_memory(view, image)
    assert coverage.min() >= 1 and coverage.max() > 1
    assert starts(2880, 1280, .25)[-1] == 1600


def test_small_inputs_have_no_redundant_tiles_and_budget_one_is_safe():
    assert flame_views(np.zeros((360,640,3), np.uint8), 1280, .25, 12) == []
    assert flame_views(np.zeros((1620,2880,3), np.uint8), 320, .25, 1) == []


def test_merge_preserves_score_and_removes_same_region_duplicates():
    high = Flame((10,20,40,80),.85,'fire')
    low = Flame((11,21,41,81),.7,'fire')
    separate = Flame((100,100,120,130),.8,'fire')
    invalid = Flame((0,0,0,1),.99,'fire')
    nan = Flame((0,0,1,1),float('nan'),'fire')
    assert merge_flames([low,high,separate,invalid,nan]) == [high,separate]


class Tensor:
    def __init__(self, data): self.data = np.array(data, dtype=float)
    def cpu(self): return self
    def numpy(self): return self.data


def result(box=(20,30,60,90), score=.8):
    return SimpleNamespace(boxes=SimpleNamespace(xyxy=Tensor([box]), conf=Tensor([score]),
        cls=Tensor([0])), names={0:'fire',1:'smoke'})


def test_tiled_model_restores_coordinates_and_batches_at_most_four(tmp_path):
    weight = tmp_path/'trusted-test-fire.pt'; weight.touch()
    calls=[]
    class Model:
        task='detect'; names={0:'fire',1:'smoke'}
        def predict(self, image, **kwargs):
            calls.append((image,kwargs))
            return [result() for _ in image] if isinstance(image,list) else [result()]
    config=ModelConfig(flame_weights=str(weight), flame_classes=['fire'],
        flame_license={k:'test' for k in ('name','source','license','usage_review')},
        flame_tiling_enabled=True,flame_tile_size=1280)
    models=LocalModels(config,factory=lambda *a,**k:Model())
    poses,flames=models.infer(np.zeros((1620,2880,3),np.uint8))
    assert poses == []
    assert len(flames) == 6  # Full-frame and first-tile duplicates merged.
    assert any(f.bbox == (1620.,370.,1660.,430.) for f in flames)
    assert all(len(image)<=4 for image,_ in calls if isinstance(image,list))
    assert all('conf' not in kwargs for _,kwargs in calls)  # No lowered threshold.


def test_pose_resize_maps_boxes_and_keypoints_to_original_frame(tmp_path):
    weight=tmp_path/'trusted-test-pose.pt';weight.touch()
    points=np.ones((1,17,3),dtype=float)
    points[:,:,0]=100;points[:,:,1]=50;points[:,:,2]=.9
    class Model:
        task='pose';model=SimpleNamespace(yaml={'kpt_shape':[17,3]})
        def predict(self,image,**kwargs):
            assert image.shape[:2] == (360,640)
            return [SimpleNamespace(boxes=SimpleNamespace(xyxy=Tensor([[10,20,200,300]]),conf=Tensor([.8])),
                keypoints=SimpleNamespace(data=Tensor(points)))]
    config=ModelConfig(pose_weights=str(weight),pose_license={k:'test' for k in ('name','source','license','usage_review')})
    models=LocalModels(config,factory=lambda *a,**k:Model())
    poses,flames=models.infer(np.zeros((1620,2880,3),np.uint8))
    assert poses[0].bbox == (45.,90.,900.,1350.)
    np.testing.assert_allclose(poses[0].keypoints[0], [450,225,.9])
    assert flames==[]


@pytest.mark.parametrize('options',[
    {'flame_tiling_enabled':'yes'},{'flame_tiling_enabled':1},
    {'flame_tile_size':64},{'flame_max_tiles':17},{'flame_tile_overlap':.9},
    {'pose_max_width':10}, {'flame_tile_size':640.5},
])
def test_invalid_tiling_configuration_rejected(options):
    config=Config()
    with pytest.raises(ValueError):
        update(config,{'models':options});config.validate()


def test_tiling_is_opt_in_for_cpu_and_legacy_configs():
    config=Config();config.validate()
    assert config.models.flame_tiling_enabled is False
