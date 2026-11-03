"""Bounded main-stream probe; never print credentials or save video."""
from pathlib import Path
import json,sys,threading
from urllib.parse import urlsplit,urlunsplit
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ai-service"))
from ai_service.config import Config
from ai_service.sources import CaptureLink,CapturedFrame

def main():
 values=dotenv_values(ROOT/".env");url=values.get("RTSP_URL") or ""
 parsed=urlsplit(url)
 if parsed.scheme!="rtsp" or parsed.path!="/stream2":
  print(json.dumps({"status":"skipped","reason":"unexpected_source"}));return
 target=urlunsplit(parsed._replace(path="/stream1"))
 config=Config();config.open_timeout_seconds=4;config.read_timeout_seconds=3
 stop=threading.Event();link=None
 report={"status":"failed","media_saved":False,"cloud_calls":False,"frames":0}
 try:
  link=CaptureLink(target,config)
  if link.open(stop):
   for _ in range(3):
    kind,frame=link.read(stop)
    if kind!="frame" or not isinstance(frame,CapturedFrame):break
    report.update(width=frame.image.shape[1],height=frame.image.shape[0]);report["frames"]+=1
   if report["frames"]:report["status"]="passed"
 except Exception:
  report["error"]="main_stream_probe_failed"
 finally:
  if link:link.close()
 out=ROOT/"runtime/small-flame-20261029/main-stream-probe.json"
 out.write_text(json.dumps(report,indent=2),encoding="utf-8")
 print(json.dumps(report))
if __name__=="__main__":main()
