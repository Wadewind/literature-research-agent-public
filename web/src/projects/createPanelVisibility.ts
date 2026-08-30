/**
 * 创建面板可见性规则：
 * - 查询未返回前不自动展开，避免刷新时深色面板闪现；
 * - 查询返回且项目为空时常驻展示，承担首次引导职责；
 * - 已有项目时默认收起为幽灵卡，把视觉重心还给项目列表；
 * - 用户点击幽灵卡后手动展开。
 */
export function shouldShowCreatePanel(
  manuallyOpened: boolean,
  projectCount: number,
  queryPending: boolean,
): boolean {
  return manuallyOpened || (!queryPending && projectCount === 0);
}
