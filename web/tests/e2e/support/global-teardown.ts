/**
 * 收尾：停掉 global-setup 起的 worker。
 *
 * 只清自己起的东西：API / 假上游 / Next 由 Playwright 的 webServer 负责回收，
 * 这里再动一遍会杀掉"复用到的"外部进程（比如开发者本机正跑着的 dev server）。
 */

import { stopWorker } from "./worker";

export default async function globalTeardown(): Promise<void> {
  stopWorker();
}
