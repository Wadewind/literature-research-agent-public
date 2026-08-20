/** 上传幂等键管理：同一文件重试复用同一 Key，选择新文件才重新生成。 */

export interface FileLike {
  name: string;
  size: number;
}

export interface UploadIntent {
  /** 本次上传的 Idempotency-Key。 */
  key: string;
  fileName: string;
  fileSize: number;
}

/** 判断已选择的文件与当前意图是否相同（同名同大小视为同一次上传）。 */
export function sameFile(intent: UploadIntent | null, file: FileLike): intent is UploadIntent {
  return intent !== null && intent.fileName === file.name && intent.fileSize === file.size;
}

/** 为文件返回应使用的上传意图：同一文件复用 Key，新文件生成新 Key。 */
export function ensureUploadIntent(
  intent: UploadIntent | null,
  file: FileLike,
  generateKey: () => string,
): UploadIntent {
  if (sameFile(intent, file)) return intent;
  return { key: generateKey(), fileName: file.name, fileSize: file.size };
}
