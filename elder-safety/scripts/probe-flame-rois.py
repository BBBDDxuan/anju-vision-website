from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ai-service"))
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2,numpy as np
from ultralytics import YOLO
out=ROOT/"runtime/small-flame-20261029"
model=YOLO(str(ROOT/"weights/luminous-fire-yolov8n.pt"),task="detect")
report={}
for name in ("near","far"):
 image=cv2.imdecode(np.frombuffer((out/f"{name}-input.png").read_bytes(),np.uint8),1)
 hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV);h,w=image.shape[:2]
 mask=(((hsv[:,:,2]>=235)&((hsv[:,:,1]<90)|((hsv[:,:,0]<40)&(hsv[:,:,1]>=90))))).astype(np.uint8)*255
 n,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
 props=[]
 for i in range(1,n):
  x,y,bw,bh,area=stats[i];cx,cy=centers[i]
  if 3<=area<=800 and bh>=3 and .1<bw/max(bh,1)<4:
   props.append((int(x),int(y),int(bw),int(bh),int(area),float(cx),float(cy)))
 props.sort(key=lambda p:-p[4]);props=props[:16]
 report[name]=[]
 for prop in props:
  x,y,bw,bh,area,cx,cy=prop
  tiles=[];positions=[]
  for side in (64,96,128,160,192,256,320):
   tw,th=min(side,w),min(side,h)
   x0=int(max(0,min(w-tw,cx-tw/2)));y0=int(max(0,min(h-th,cy-th/2)))
   tiles.append(image[y0:y0+th,x0:x0+tw]);positions.append((x0,y0,side))
  detections=[]
  for res,(x0,y0,side) in zip(model.predict(tiles,imgsz=640,conf=.1,device="0",verbose=False),positions):
   for b,c,k in zip(res.boxes.xyxy.cpu().numpy(),res.boxes.conf.cpu().numpy(),res.boxes.cls.cpu().numpy()):
    if res.names[int(k)]!="fire":continue
    b=[float(b[0])+x0,float(b[1])+y0,float(b[2])+x0,float(b[3])+y0]
    if b[0]-3<=cx<=b[2]+3 and b[1]-3<=cy<=b[3]+3:
     detections.append({"side":side,"score":round(float(c),4),"box":[round(v,1) for v in b]})
  item={"proposal":prop,"detections":detections}
  report[name].append(item)
  print(json.dumps({"name":name,**item}))
 (out/"roi-sweep.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
