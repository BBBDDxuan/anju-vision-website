import { Icon } from './ui';

export function HomeScene() {
  return <figure className="home-scene-card">
    <div className="home-scene-header"><span><Icon name="home" size={16}/>居家关怀，从日常开始</span></div>
    <div className="home-scene-image">
      <img src="/assets/home-care-scene-water-reflection.png" width={1536} height={1024} fetchPriority="high" alt="居家功能示意：老人前脚附近的木地板上有带窗户倒影、水纹与水滴的透明水洼，身体失衡前倾、膝盖弯曲，伸手扶向茶桌，红框标出跌倒风险；桌上的钥匙与扶手椅上的蓝色包分别用蓝框和绿框标出。画面与标注均为示意。"/>
      <div className="scene-annotations" aria-hidden="true">
        <div className="scene-target scene-person"><span>跌倒风险</span></div>
        <div className="scene-target scene-keys"><span>钥匙</span></div>
        <div className="scene-target scene-bag"><span>包</span></div>
      </div>
      <div className="scene-fire-inset">
        <img src="/assets/kitchen-fire-scene.png" width={1536} height={1024} alt="AI 火情功能示意：锅内的可见明火区域用橙红色候选框标出。"/>
        <div className="scene-fire-target" aria-hidden="true"/>
        <span>疑似明火</span>
      </div>
    </div>
    <figcaption><span><Icon name="video" size={15}/>人物异常</span><span><Icon name="shield" size={15}/>家庭危险</span><span><Icon name="search" size={15}/>物品追踪</span></figcaption>
  </figure>;
}
