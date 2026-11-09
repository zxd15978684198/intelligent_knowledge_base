"use client";

import * as React from "react";

/**
 * 「打字机」匀速放出。
 *
 * 为什么需要它：模型不是逐字输出的。实测同一台机器上，一个 602 字的回答
 * 首 token 要 4.8s，之后 2.1s 内全部吐完（约 287 字/秒），而且网关常把整句
 * 合成一帧（单帧 163 字）。SSE 链路本身是逐帧的，但"一次跳一百多字"在屏幕上
 * 看起来就是突然出现。这里把已经收到的字符先缓冲，再按固定速度放到界面上。
 *
 * 三条边界：
 * 1. **只减速、不增删**。放出的一定是服务端已经发来的字符，顺序与内容都不改。
 * 2. **流结束立即补齐**。`enabled` 变 false（收到 done 或被中止）就一次放完，
 *    否则"答案已经完整但屏幕还在慢慢吐"会让人误以为卡住。
 * 3. **尊重 reduced-motion**。用户在系统里要求减少动态效果时不做逐字动画。
 */

export interface TypewriterOptions {
  /** 流还开着：为 false 时立即放完（收到 done、被中止、或渲染历史轮次）。 */
  readonly enabled: boolean;
  /** 基准速度（字符/秒）。积压越多越快，但不会超过它的若干倍。 */
  readonly charsPerSecond?: number;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/** 流结束后剩余不足这么多字，就直接补齐而不是继续匀速（见 effect 里的说明）。 */
const FLUSH_THRESHOLD_CHARS = 160;

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/**
 * 返回"当前应该显示到第几个字符"的切片后的文本。
 *
 * 速度策略：积压 ≤ 3 倍基准速度时按基准速度放；超过后按 `积压 / 4` 提速
 * （即最多落后 4 秒），并封顶 6 倍基准速度，避免长回答末尾拖出一条长尾。
 */
export function useTypewriter(text: string, options: TypewriterOptions): string {
  const { enabled, charsPerSecond = 45 } = options;
  const [revealed, setRevealed] = React.useState(text.length);
  /** 供定时器读取"当前放到哪了"，避免把 revealed 放进 effect 依赖导致每帧重建定时器。 */
  const revealedRef = React.useRef(revealed);
  revealedRef.current = revealed;

  React.useEffect(() => {
    if (prefersReducedMotion()) {
      setRevealed(text.length);
      return;
    }

    // 流已结束且只剩一小截没放完：直接补齐。攒着几十个字再慢慢吐，既看不出区别，
    // 又会让"答案已经完整"的界面迟到半秒（列表、复制、读屏都受影响）。
    if (!enabled && text.length - revealedRef.current <= FLUSH_THRESHOLD_CHARS) {
      setRevealed(text.length);
      return;
    }

    // 新一轮提问（文本变短）或首帧到达前的空文本：把游标退回边界，不要显示上一轮的尾巴。
    setRevealed((previous) => Math.min(previous, text.length));

    let last = performance.now();

    // 收到 done 之后不立刻全丢出来，而是按 3 倍速继续放完：否则流一结束就"啪"地
    // 补齐剩下几百字，等于把前面的匀速全白费。历史轮次挂载时 `revealed` 初值就是全长，
    // 这里不会重放动画。
    const finishMultiplier = enabled ? 1 : 3;

    // 用 setInterval 而不是 requestAnimationFrame：浏览器会把后台标签页的 rAF
    // 完全暂停（实测这里是窗口失焦的页面），那样"放字"就永远不推进，直到 done
    // 一次性补齐——等于没有打字机。定时器最差被节流到 1s 一次，仍在前进。
    const timer = window.setInterval(() => {
      // 已经放到头就停表：一轮回答挂着一个 60ms 定时器不划算，多轮对话会越积越多。
      if (revealedRef.current >= text.length) {
        window.clearInterval(timer);
        return;
      }
      const now = performance.now();
      // 被节流或页面休眠时 `now - last` 可能是几十秒，夹一下避免一次放完整段。
      const seconds = Math.min((now - last) / 1000, 0.5);
      last = now;
      setRevealed((previous) => {
        const target = text.length;
        if (previous >= target) {
          return previous;
        }
        const backlog = target - previous;
        const base = charsPerSecond * finishMultiplier;
        // 积压超过 3 倍基准速度就提速，但最多落后 6 秒、且不超过 3 倍基准速度——
        // 上限是刻意的：再快就回到"一屏刷完"的观感，等于没做打字机。
        const speed = backlog > base * 3 ? Math.min(backlog / 6, base * 3) : base;
        return Math.min(target, previous + Math.max(1, Math.round(speed * seconds)));
      });
    }, 60);

    return () => {
      window.clearInterval(timer);
    };
  }, [charsPerSecond, enabled, text.length]);

  return text.slice(0, Math.min(revealed, text.length));
}
