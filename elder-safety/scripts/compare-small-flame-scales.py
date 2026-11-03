"""Exploratory offline model-only scale sweep; never lower live alert thresholds."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ai-service"))
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2,numpy as np,torch
from ultralytics import YOLO

def main():
    out=ROOT/"runtime/small-flame-20261029"
    model=YOLO(str(ROOT/"weights/luminous-fire-yolov8n.pt"),task="detect")
    report={}
    for name in ("near","far"):
        image=cv2.imdecode(np.frombuffer((out/f"{name}-input.png").read_bytes(),np.uint8),1)
        h,w=image.shape[:2]; report[name]={}
        for side in (128,160,192,256,320,384,480,640):
            th,tw=min(side,h),min(side,w)
            xs=list(range(0,w-tw+1,max(1,tw//2)))
            ys=list(range(0,h-th+1,max(1,th//2)))
            xs=sorted(set(xs+[w-tw]));ys=sorted(set(ys+[h-th]))
            tiles=[image[y:y+th,x:x+tw] for y in ys for x in xs]
            offsets=[(x,y) for y in ys for x in xs]
            best=[]
            for res,(x,y) in zip(model.predict(tiles,imgsz=640,conf=.01,device="0",verbose=False),offsets):
                for b,c,k in zip(res.boxes.xyxy.cpu().numpy(),res.boxes.conf.cpu().numpy(),res.boxes.cls.cpu().numpy()):
                    if res.names[int(k)]=="fire": best.append({"score":round(float(c),4),"box":[round(float(b[0])+x,1),round(float(b[1])+y,1),round(float(b[2])+x,1),round(float(b[3])+y,1)],"offset":[x,y]})
            best.sort(key=lambda v:-v["score"])
            report[name][str(side)]={"tile_count":len(tiles),"top":best[:8]}
            print(json.dumps({"name":name,"side":side,"tiles":len(tiles),"top":best[:4]}))
    (out/"scale-sweep.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
if __name__=="__main__":main()
