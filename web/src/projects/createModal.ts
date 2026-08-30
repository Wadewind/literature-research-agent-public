/**
 * 空态首次进入时自动打开创建 Modal，承担首次引导：
 * - 查询未返回前不自动打开，避免刷新时闪现；
 * - 确认无项目时自动打开一次；
 * - 用户关闭后本次访问内不再自动打开。
 */
export function shouldAutoOpenCreateModal(
  alreadyAutoOpened: boolean,
  projectCount: number,
  queryPending: boolean,
): boolean {
  return !alreadyAutoOpened && !queryPending && projectCount === 0;
}
