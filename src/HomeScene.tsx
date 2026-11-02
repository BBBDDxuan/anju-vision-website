import { Icon } from './ui';

export function HomeScene() {
  return <figure className="home-scene-card">
    <div className="home-scene-header"><span><Icon name="home" size={16}/>居家关怀，从日常开始</span><span className="scene-concept-label">AI 生成场景示意</span></div>
    <div className="home-scene-image">
      <img src="/assets/home-care-scene.png" width={1536} height={1024} fetchPriority="high" alt="温暖的居家场景示意：老人拄杖走过客厅，周围有沙发、茶桌、绿植和墙面摄像头。"/>
      <div className="home-scene-caption"><Icon name="shield" size={18}/><span>关注日常活动，留意需要帮助的时刻。</span></div>
    </div>
    <figcaption><span><Icon name="video" size={15}/>人物活动</span><span><Icon name="clock" size={15}/>关键片段</span><span><Icon name="search" size={15}/>原片证据</span><small>场景示意，非实拍或检测结果</small></figcaption>
  </figure>;
}
