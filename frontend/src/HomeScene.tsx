import { useEffect, useRef, useState } from 'react';
import { Icon } from './ui';

function TargetOutline() {
  return <svg className="scene-target-outline" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false"><rect x="1" y="1" width="98" height="98" rx="1" pathLength="1" vectorEffect="non-scaling-stroke"/></svg>;
}

export function HomeScene() {
  const stage = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const played = useRef(false);
  const [imageReady, setImageReady] = useState(false);
  const [playback, setPlayback] = useState(0);

  useEffect(() => {
    if (image.current?.complete && image.current.naturalWidth) setImageReady(true);
  }, []);

  useEffect(() => {
    if (!imageReady || !stage.current) return;
    let visible = false;
    const start = () => {
      if (!visible || played.current || document.visibilityState === 'hidden') return;
      played.current = true;
      setPlayback(value => value + 1);
    };
    const observer = new IntersectionObserver(entries => {
      visible = entries.some(item => item.isIntersecting);
      start();
    }, { threshold: 0.2 });
    observer.observe(stage.current);
    document.addEventListener('visibilitychange', start);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', start);
    };
  }, [imageReady]);

  return <figure className="home-scene-card" data-demo-playing={playback > 0}>
    <div className="home-scene-header"><span><Icon name="home" size={16}/>居家关怀，从日常开始</span><button type="button" className="scene-replay" disabled={!imageReady} onClick={() => setPlayback(value => value + 1)} aria-label="重播居家识别示意"><Icon name="replay" size={14}/>重播演示</button></div>
    <div ref={stage} className="home-scene-image">
      <img ref={image} onLoad={() => setImageReady(true)} src="/assets/home-care-scene-water-reflection.png" width={1536} height={1024} fetchPriority="high" alt="居家功能示意：老人前脚附近的木地板上有带窗户倒影、水纹与水滴的透明水洼，身体失衡前倾、膝盖弯曲，伸手扶向茶桌，红框标出跌倒风险；桌上的钥匙与扶手椅上的蓝色包分别用蓝框和绿框标出。画面与标注均为示意。"/>
      <div key={playback} className="scene-demo-layer">
        <div className="scene-scan" aria-hidden="true"/>
        <div className="scene-annotations" aria-hidden="true">
          <div className="scene-target scene-person"><TargetOutline/><span>跌倒风险</span></div>
          <div className="scene-target scene-keys"><TargetOutline/><span>钥匙</span></div>
          <div className="scene-target scene-bag"><TargetOutline/><span>包</span></div>
        </div>
        <div className="scene-fire-inset">
          <img src="/assets/kitchen-fire-scene.png" width={1536} height={1024} alt="AI 火情功能示意：锅内的可见明火区域用橙红色候选框标出。"/>
          <div className="scene-fire-target" aria-hidden="true"><TargetOutline/></div>
          <span>疑似明火</span>
        </div>
      </div>
    </div>
    <figcaption><span><Icon name="video" size={15}/>人物异常</span><span><Icon name="shield" size={15}/>家庭危险</span><span><Icon name="search" size={15}/>物品追踪</span></figcaption>
  </figure>;
}
