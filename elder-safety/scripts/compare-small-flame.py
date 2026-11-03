"""Offline diagnostic on user supplied screenshots; no camera/network/push."""
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service"))
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2
import numpy as np
import torch
from ultralytics import YOLO

def decode(path):
    return cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)

def save(path, image):
    ok, data=cv2.imencode(".png", image)
    if not ok: raise RuntimeError("encode failed")
    path.write_bytes(data.tobytes())

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--near",type=Path,required=True,help="Near-fire screenshot")
    parser.add_argument("--far",type=Path,required=True,help="Far-fire screenshot")
    args=parser.parse_args()
    out=ROOT/"runtime/small-flame-20261029"
    out.mkdir(parents=True,exist_ok=True)
    sources=[("near",args.near,(2,177,1292,900)),
             ("far",args.far,(0,0,1285,684))]
    model=YOLO(str(ROOT/"weights/luminous-fire-yolov8n.pt"),task="detect")
    torch.set_num_threads(4)
    report={"scope":"two supplied screenshots, not independent accuracy evaluation", "inputs":[]}
    for name,path,box in sources:
        raw=decode(path); x1,y1,x2,y2=box
        crop=raw[y1:y2,x1:x2].copy()
        image=cv2.resize(crop,(640,round(crop.shape[0]*640/crop.shape[1])))
        save(out/f"{name}-input.png",image)
        item={"name":name,"screenshot_crop":box,"input_shape":list(image.shape),"modes":{}}
        for size in (640,960,1280):
            t=time.perf_counter()
            res=model.predict(image,imgsz=size,conf=.25,device="0",verbose=False)[0]
            found=[]
            for b,c,k in zip(res.boxes.xyxy.cpu().numpy(),res.boxes.conf.cpu().numpy(),res.boxes.cls.cpu().numpy()):
                if res.names[int(k)]=="fire": found.append({"box":[round(float(v),2) for v in b],"score":round(float(c),5)})
            item["modes"][f"full_{size}"]={"detections":found,"ms":round((time.perf_counter()-t)*1000,2)}
        h,w=image.shape[:2]
        tiles=[];positions=[]
        for y in (0,max(0,h-256)):
            for x in (0,192,w-256):
                tiles.append(image[y:y+256,x:x+256]);positions.append((x,y))
        t=time.perf_counter()
        results=model.predict(tiles,imgsz=640,conf=.25,device="0",verbose=False)
        found=[]
        for res,(x,y) in zip(results,positions):
            for b,c,k in zip(res.boxes.xyxy.cpu().numpy(),res.boxes.conf.cpu().numpy(),res.boxes.cls.cpu().numpy()):
                if res.names[int(k)]=="fire":
                    found.append({"box":[round(float(b[0])+x,2),round(float(b[1])+y,2),round(float(b[2])+x,2),round(float(b[3])+y,2)],"score":round(float(c),5),"offset":[x,y]})
        item["modes"]["tiles_256"]={"detections":found,"ms":round((time.perf_counter()-t)*1000,2)}
        report["inputs"].append(item)
        print(json.dumps(item,ensure_ascii=True))
    (out/"baseline.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__": main()
