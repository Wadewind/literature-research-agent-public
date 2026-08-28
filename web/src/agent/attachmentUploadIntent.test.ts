import { describe, expect, it } from "vitest";

import { ensureAgentAttachmentUploadIntent } from "./attachmentUploadIntent";

const file = {
  name: "notes.txt",
  size: 5,
  type: "text/plain",
  lastModified: 1_787_875_200_000,
};

describe("ensureAgentAttachmentUploadIntent", () => {
  it("失败后重选同一文件复用上传幂等键", () => {
    const first = ensureAgentAttachmentUploadIntent(null, file, () => "key-1");
    const retry = ensureAgentAttachmentUploadIntent(first, { ...file }, () => "key-2");

    expect(retry).toBe(first);
  });

  it.each([
    { ...file, name: "other.txt" },
    { ...file, size: 6 },
    { ...file, type: "text/markdown" },
    { ...file, lastModified: file.lastModified + 1 },
  ])("选择不同文件身份时生成新幂等键", (changed) => {
    const first = ensureAgentAttachmentUploadIntent(null, file, () => "key-1");
    const next = ensureAgentAttachmentUploadIntent(first, changed, () => "key-2");

    expect(next.key).toBe("key-2");
  });
});
