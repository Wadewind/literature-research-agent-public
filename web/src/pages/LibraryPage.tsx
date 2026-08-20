/** Project 内文献库：PDF 上传（带 Idempotency-Key）与 Paper 列表。 */

import { useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project, UploadResult } from "../api/types";
import { ensureUploadIntent, type UploadIntent } from "../library/uploadIntent";

function formatSize(sizeBytes: number): string {
  if (sizeBytes >= 1024 * 1024) return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
  if (sizeBytes >= 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${sizeBytes} B`;
}

export default function LibraryPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [intent, setIntent] = useState<UploadIntent | null>(null);
  const [lastUpload, setLastUpload] = useState<UploadResult | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });

  const papersQuery = useQuery({
    queryKey: ["papers", projectId],
    queryFn: () => apiFetch<PaperListItem[]>(`/api/v1/projects/${projectId}/papers`),
    // 上传后解析在后台进行：有未完成的 Paper 时轮询刷新列表
    refetchInterval: (query) =>
      query.state.data?.some((p) => p.latest_version && !p.latest_version.parse_ready)
        ? 3000
        : false,
  });

  const uploadMutation = useMutation({
    mutationFn: (input: { file: File; key: string }) => {
      const form = new FormData();
      form.append("file", input.file);
      return apiFetch<UploadResult>(`/api/v1/projects/${projectId}/paper-files`, {
        method: "POST",
        headers: { "Idempotency-Key": input.key },
        body: form,
      });
    },
    onSuccess: (result) => {
      setLastUpload(result);
      setFile(null);
      setIntent(null);
      void queryClient.invalidateQueries({ queryKey: ["papers", projectId] });
    },
  });

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
    uploadMutation.reset();
    if (selected) {
      // 同一文件重试复用同一 Key；选择新文件才生成新 Key
      setIntent((prev) => ensureUploadIntent(prev, selected, () => crypto.randomUUID()));
    } else {
      setIntent(null);
    }
  };

  const onUpload = () => {
    if (!file || !intent) return;
    uploadMutation.mutate({ file, key: intent.key });
  };

  if (projectQuery.isError) {
    return (
      <section className="panel">
        <p className="error-text">{errorMessage(projectQuery.error)}</p>
        <Link to="/">返回 Project 列表</Link>
      </section>
    );
  }

  return (
    <div className="stack">
      <section className="panel">
        <p className="breadcrumb">
          <Link to="/">Project</Link> / {projectQuery.data?.name ?? "…"}
        </p>
        <h1>{projectQuery.data?.name ?? "文献库"}</h1>

        <h2>上传 PDF</h2>
        <div className="form-row">
          <input type="file" accept="application/pdf,.pdf" onChange={onFileChange} />
          <button
            type="button"
            onClick={onUpload}
            disabled={!file || !intent || uploadMutation.isPending}
          >
            {uploadMutation.isPending ? "上传中…" : "开始导入"}
          </button>
        </div>
        {uploadMutation.isError && (
          <p className="error-text">{errorMessage(uploadMutation.error)}</p>
        )}
        {lastUpload && (
          <p className="success-text">
            已受理导入任务（{lastUpload.status}）：
            <Link to={`/runs/${lastUpload.run_id}`}>查看 Run 进度</Link>
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Paper 列表</h2>
        {papersQuery.isPending && <p className="muted">加载中…</p>}
        {papersQuery.isError && (
          <p className="error-text">{errorMessage(papersQuery.error)}</p>
        )}
        {papersQuery.data && papersQuery.data.length === 0 && (
          <p className="muted">文献库为空。上传第一份 PDF 开始。</p>
        )}
        {papersQuery.data && papersQuery.data.length > 0 && (
          <table className="paper-table">
            <thead>
              <tr>
                <th>文件</th>
                <th>大小</th>
                <th>解析状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {papersQuery.data.map((paper) => {
                const version = paper.latest_version;
                return (
                  <tr key={paper.paper_id}>
                    <td>
                      {version ? version.display_filename : "（无版本）"}
                      <span className="mono muted"> {paper.paper_id.slice(0, 8)}</span>
                    </td>
                    <td>{version ? formatSize(version.size_bytes) : "—"}</td>
                    <td>
                      {version ? (
                        version.parse_ready ? (
                          <span className="badge badge-ok">已解析</span>
                        ) : (
                          <span className="badge badge-pending">待解析 / 解析中</span>
                        )
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {version && (
                        <>
                          <a
                            href={`/api/v1/projects/${projectId}/paper-versions/${version.version_id}/file`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            原文
                          </a>
                          {" · "}
                          {version.parse_ready ? (
                            <Link
                              to={`/projects/${projectId}/versions/${version.version_id}/document`}
                            >
                              结构预览
                            </Link>
                          ) : (
                            <span className="muted">结构预览</span>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
