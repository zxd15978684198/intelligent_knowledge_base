import { describe, expect, it } from "vitest";

import {
  ANSWER_VERDICTS,
  answerVerdictSchema,
  isJobTerminal,
  JOB_STATUSES,
  jobStatusSchema,
  RUN_STATUSES,
  runStatusSchema,
} from "@/lib/contracts";

import { sampleJobProcessing, sampleJobSuperseded } from "../fixtures/entities";

describe("作业状态：六态", () => {
  it("恰好六态，顺序与后端一致", () => {
    expect(JOB_STATUSES).toEqual(["pending", "processing", "succeeded", "failed", "cancelled", "superseded"]);
    for (const status of JOB_STATUSES) {
      expect(jobStatusSchema.safeParse(status).success).toBe(true);
    }
  });

  it("不在闭集里的状态被拒绝（拼错/新增状态必须显式处理）", () => {
    expect(jobStatusSchema.safeParse("queued").success).toBe(false);
    expect(jobStatusSchema.safeParse("SUCCEEDED").success).toBe(false);
    expect(jobStatusSchema.safeParse("").success).toBe(false);
  });
});

describe("isJobTerminal：terminal 漏了 superseded 的兜底", () => {
  it("processing / pending 不是终态", () => {
    expect(isJobTerminal({ status: "processing", terminal: false })).toBe(false);
    expect(isJobTerminal({ status: "pending", terminal: false })).toBe(false);
  });

  it("terminal=true 直接是终态", () => {
    expect(isJobTerminal({ status: "succeeded", terminal: true })).toBe(true);
    expect(isJobTerminal({ status: "cancelled", terminal: true })).toBe(true);
  });

  it("superseded 且 terminal=false（后端真实行为）仍判为终态", () => {
    // 后端 TERMINAL_STATUSES（services/queue/jobs.py:31）漏了 superseded，
    // 只看 terminal 会永远轮询一个已结束的作业。
    expect(sampleJobSuperseded.terminal).toBe(false);
    expect(isJobTerminal(sampleJobSuperseded)).toBe(true);
  });

  it("夹具本身与判据一致：processing 还在跑", () => {
    expect(isJobTerminal(sampleJobProcessing)).toBe(false);
  });

  it("多出来的字段不影响判据（函数只读 status/terminal）", () => {
    expect(isJobTerminal({ ...sampleJobProcessing, status: "superseded" })).toBe(true);
  });
});

describe("问答结论：三态", () => {
  it("恰好三态且大写", () => {
    expect(ANSWER_VERDICTS).toEqual(["SUPPORTED", "INSUFFICIENT", "CONFLICTING"]);
    for (const verdict of ANSWER_VERDICTS) {
      expect(answerVerdictSchema.safeParse(verdict).success).toBe(true);
    }
  });

  it("小写/未知值被拒绝（后端是 StrEnum，不是任意字符串）", () => {
    expect(answerVerdictSchema.safeParse("supported").success).toBe(false);
    expect(answerVerdictSchema.safeParse("PARTIALLY_SUPPORTED").success).toBe(false);
  });
});

describe("RunStatus：done.status 的五态", () => {
  it("恰好五态", () => {
    expect(RUN_STATUSES).toEqual(["COMPLETED", "ABSTAINED", "TIMEOUT", "BUDGET_EXCEEDED", "FAILED"]);
  });

  it("拒绝非枚举值", () => {
    expect(runStatusSchema.safeParse("DONE").success).toBe(false);
    expect(runStatusSchema.safeParse("completed").success).toBe(false);
  });
});
