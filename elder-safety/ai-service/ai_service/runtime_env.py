"""Keep model-library state alongside the project, before third-party imports."""
from pathlib import Path
import os


def configure_model_runtime():
    root = Path(__file__).resolve().parents[2]
    mapping = {"YOLO_CONFIG_DIR": root / "runtime" / "ultralytics",
               "TORCH_HOME": root / "runtime" / "torch",
               "HF_HOME": root / "runtime" / "huggingface",
               "MPLCONFIGDIR": root / "runtime" / "matplotlib"}
    for key, directory in mapping.items():
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(directory)
    (mapping["YOLO_CONFIG_DIR"] / "Ultralytics").mkdir(exist_ok=True)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ["YOLO_OFFLINE"] = "true"
    return root
