export const introductionPages = [
  { id: 'overview', label: '产品概览', icon: 'home', summary: '从日常画面，到有据可查的关怀。' },
  { id: 'capabilities', label: '核心能力', icon: 'shield', summary: '人物异常、家庭危险与物品追踪。' },
  { id: 'how-it-works', label: '使用流程', icon: 'clock', summary: '导入、分析、回看，三步开始。' },
] as const;

export type IntroductionPage = typeof introductionPages[number]['id'];
export const introductionFromHash = (hash: string): IntroductionPage =>
  introductionPages.find(page => `#${page.id}` === hash)?.id || 'overview';
export const introductionHref = (page: IntroductionPage) => page === 'overview' ? '/' : `/#${page}`;
