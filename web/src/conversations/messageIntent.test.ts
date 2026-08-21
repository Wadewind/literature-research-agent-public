/** 提问幂等意图测试：新问题新 Key，原内容重试复用同一 Key。 */

import { describe, expect, it } from "vitest";

import { ensureMessageIntent } from "./messageIntent";

let counter = 0;
const nextKey = () => `message-key-${++counter}`;

describe("ensureMessageIntent", () => {
  it("首次提交生成新 Key", () => {
    const intent = ensureMessageIntent(null, "什么是 RAG？", nextKey);
    expect(intent).toEqual({ content: "什么是 RAG？", key: "message-key-1" });
  });

  it("相同问题重试复用既有 Key", () => {
    const first = ensureMessageIntent(null, "什么是 RAG？", nextKey);
    const retried = ensureMessageIntent(first, "什么是 RAG？", nextKey);
    expect(retried).toBe(first);
  });

  it("问题内容改变后生成新 Key", () => {
    const first = ensureMessageIntent(null, "问题 A", nextKey);
    const changed = ensureMessageIntent(first, "问题 B", nextKey);
    expect(changed.key).not.toBe(first.key);
  });
});
