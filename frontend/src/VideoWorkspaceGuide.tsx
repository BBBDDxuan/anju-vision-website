import { Icon } from './ui';

const steps = [
  { title: '导入录像', description: '选择本地居家录像，或从已有录像中选择。' },
  { title: '选择分析模式', description: '根据需要选择安全分析或物体追踪。' },
  { title: '开始分析并查看结果', description: '查看识别结果、事件提醒和相关视频片段。' },
];

const capabilities = [
  { icon: 'activity', title: '跌倒识别', description: '发现人体跌倒及异常姿态。' },
  { icon: 'help-gesture', title: '挥手动作识别', description: '识别画面中的连续挥手动作。' },
  { icon: 'flame', title: '明火识别', description: '发现画面中的明火区域。' },
];

export function VideoAnalysisGuide() {
  return <aside className="workspace-workflow" aria-label="视频分析使用指南">
    <h2><Icon name="video" size={18}/>分析流程</h2>
    <ol className="workspace-workflow-steps">{steps.map((step, index) => <li key={step.title}>
      <span className="workspace-step-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
      <div><h3>{step.title}</h3><p>{step.description}</p></div>
    </li>)}</ol>
  </aside>;
}

export function VideoRecognitionCapabilities() {
  return <section className="workspace-capability-grid" aria-label="核心识别能力">
    {capabilities.map((capability, index) => <article className="workspace-capability-card" key={capability.title}>
      <span className="workspace-capability-icon"><Icon name={capability.icon} size={23}/></span>
      <div><h2><span>{String(index + 1).padStart(2, '0')}</span>{capability.title}</h2><p>{capability.description}</p></div>
    </article>)}
  </section>;
}
