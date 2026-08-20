/** 上传幂等键逻辑测试：同一文件复用 Key，新文件生成新 Key。 */

import { describe, expect, it } from "vitest";

import { ensureUploadIntent, sameFile } from "./uploadIntent";

let counter = 0;
const nextKey = () => `key-${++counter}`;

describe("ensureUploadIntent", () => {
  it("首次选择文件生成新 Key", () => {
    const intent = ensureUploadIntent(null, { name: "a.pdf", size: 10 }, nextKey);
    expect(intent.key).toBe("key-1");
    expect(intent.fileName).toBe("a.pdf");
  });

  it("同一文件（同名同大小）复用已有 Key", () => {
    const first = ensureUploadIntent(null, { name: "a.pdf", size: 10 }, nextKey);
    const second = ensureUploadIntent(first, { name: "a.pdf", size: 10 }, nextKey);

    expect(second).toBe(first);
  });

  it("选择新文件生成新 Key", () => {
    const first = ensureUploadIntent(null, { name: "a.pdf", size: 10 }, nextKey);
    const renamed = ensureUploadIntent(first, { name: "b.pdf", size: 10 }, nextKey);
    const resized = ensureUploadIntent(first, { name: "a.pdf", size: 20 }, nextKey);

    expect(renamed.key).not.toBe(first.key);
    expect(resized.key).not.toBe(first.key);
  });
});

describe("sameFile", () => {
  it("无意图时返回 false", () => {
    expect(sameFile(null, { name: "a.pdf", size: 1 })).toBe(false);
  });
});
