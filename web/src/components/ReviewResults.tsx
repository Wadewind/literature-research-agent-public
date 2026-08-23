import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiFetch, errorMessage } from "../api/client";
import type {
  EvidenceDetail,
  HumanInputRequest,
  ReviewArtifact,
  ReviewOutput,
} from "../api/types";
import {
  ensureHumanInputIntent,
  isHumanInputConflictStatus,
  type HumanInputIntent,
  type HumanInputSubmission,
} from "../reviews/reviewHumanInput";
import {
  artifactContentUrl,
  evidenceFileUrl,
  matrixRows,
  moveOutlineSection,
  nextOutlineSection,
  outlineDraftDirty,
  outlineDraftIssues,
  outlineSections,
  sectionResult,
  visibleDimensionKeys,
  type OutlineSection,
} from "../reviews/reviewResults";

interface ReviewResultsProps {
  projectId: string;
  runId: string;
  request: HumanInputRequest | null;
  eventSequence: number;
  citationsValidated: boolean;
  canSubmitHumanInput: boolean;
}

function noRetryForMissing(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status === 404) return false;
  return failureCount < 1;
}

function isNotReady(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

function updateSection(
  sections: OutlineSection[],
  index: number,
  change: Partial<OutlineSection>,
): OutlineSection[] {
  return sections.map((section, sectionIndex) =>
    sectionIndex === index ? { ...section, ...change } : section,
  );
}

function EvidenceLocator({ projectId, evidenceId }: { projectId: string; evidenceId: string }) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["evidence", projectId, evidenceId],
    queryFn: () => apiFetch<EvidenceDetail>(`/api/v1/projects/${projectId}/evidence/${evidenceId}`),
    enabled: open,
    retry: noRetryForMissing,
  });
  const evidence = query.data;
  return (
    <span className="review-evidence-locator">
      <button className="evidence-chip" type="button" onClick={() => setOpen((value) => !value)}>
        Evidence {evidenceId.slice(0, 8)}
      </button>
      {open && (
        <span className="evidence-popover">
          {query.isPending && "正在读取定位…"}
          {query.isError && <span className="error-text">{errorMessage(query.error)}</span>}
          {evidence && (
            <>
              <span>{evidence.excerpt}</span>
              <small>{evidence.section_path ?? "未标注章节"} · {evidence.page_start ? `第 ${evidence.page_start} 页` : "无页码"}</small>
              <a href={evidenceFileUrl(projectId, evidence.version_id, evidence.page_start)} target="_blank" rel="noreferrer">跳到 PDF 原页</a>
            </>
          )}
        </span>
      )}
    </span>
  );
}

function OutlineWorkbench({
  projectId,
  runId,
  output,
  request,
  availableDimensions,
  canSubmit,
}: {
  projectId: string;
  runId: string;
  output: ReviewOutput | undefined;
  request: HumanInputRequest | null;
  availableDimensions: string[];
  canSubmit: boolean;
}) {
  const queryClient = useQueryClient();
  const parsed = outlineSections(output);
  const [sections, setSections] = useState<OutlineSection[]>(parsed);
  const [feedback, setFeedback] = useState("");
  const intent = useRef<HumanInputIntent | null>(null);

  const mutation = useMutation({
    mutationFn: ({ submission, key }: { submission: HumanInputSubmission; key: string }) =>
      apiFetch(`/api/v1/projects/${projectId}/reviews/${runId}/outline-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify({
          request_id: submission.requestId,
          request_version: submission.requestVersion,
          outline_output_id: submission.outlineOutputId,
          action: submission.action,
          payload: submission.payload,
        }),
      }),
    onSuccess: () => {
      intent.current = null;
      setFeedback("");
      void queryClient.invalidateQueries({ queryKey: ["review", projectId, runId] });
      void queryClient.invalidateQueries({ queryKey: ["review-outline", projectId, runId] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", projectId] });
    },
    onError: (error) => {
      if (!(error instanceof ApiError) || !isHumanInputConflictStatus(error.status)) return;
      void queryClient.invalidateQueries({ queryKey: ["review", projectId, runId] });
      void queryClient.invalidateQueries({ queryKey: ["review-outline", projectId, runId] });
      void queryClient.invalidateQueries({ queryKey: ["review-matrix", projectId, runId] });
    },
  });

  const submit = (action: HumanInputSubmission["action"], payload: Record<string, unknown>) => {
    if (!request) return;
    const submission: HumanInputSubmission = {
      requestId: request.request_id,
      requestVersion: request.request_version,
      outlineOutputId: request.outline_output_id,
      action,
      payload,
    };
    intent.current = ensureHumanInputIntent(intent.current, submission, () => crypto.randomUUID());
    mutation.mutate({ submission, key: intent.current.key });
  };

  if (!output) return <div className="empty-state compact"><h3>大纲尚未生成</h3><p>Evidence Matrix 完成后，版本化大纲会显示在这里。</p></div>;
  if (parsed.length === 0) return <p className="error-text">大纲 Output 不符合 outline.v1 展示契约。</p>;

  const requestMatches = canSubmit && request?.outline_output_id === output.output_id;
  const allowed = new Set(request?.allowed_actions ?? []);
  const dirty = outlineDraftDirty(parsed, sections);
  const draftIssues = outlineDraftIssues(sections, availableDimensions);
  return (
    <div className="outline-workbench">
      <div className="outline-version-row">
        <span className="mono">outline.v{output.version}</span>
        {requestMatches && <span className="badge badge-warn">Request v{request.request_version} 等待输入</span>}
      </div>
      <ol className="outline-section-list">
        {sections.map((section, index) => (
          <li key={index}>
            <span className="outline-number">{String(index + 1).padStart(2, "0")}</span>
            {requestMatches && allowed.has("edit") ? (
              <div className="outline-fields">
                <label>section_key<input value={section.section_key} maxLength={64} className="mono" onChange={(event) => setSections((current) => updateSection(current, index, { section_key: event.target.value }))}/></label>
                <label>章节标题<input value={section.title} maxLength={200} onChange={(event) => setSections((current) => updateSection(current, index, { title: event.target.value }))}/></label>
                <label>章节目标<textarea value={section.purpose} maxLength={1000} rows={2} onChange={(event) => setSections((current) => updateSection(current, index, { purpose: event.target.value }))}/></label>
                <fieldset><legend>分析维度</legend><div className="dimension-options">{availableDimensions.map((key) => <label key={key}><input type="checkbox" checked={section.dimension_keys.includes(key)} onChange={(event) => setSections((current) => updateSection(current, index, { dimension_keys: event.target.checked ? [...section.dimension_keys, key] : section.dimension_keys.filter((item) => item !== key) }))}/>{key}</label>)}</div></fieldset>
                <div className="outline-row-actions"><button className="button-plain" type="button" disabled={index === 0} onClick={() => setSections((current) => moveOutlineSection(current, index, -1))}>上移</button><button className="button-plain" type="button" disabled={index === sections.length - 1} onClick={() => setSections((current) => moveOutlineSection(current, index, 1))}>下移</button><button className="button-text-danger" type="button" disabled={sections.length === 1} onClick={() => setSections((current) => current.filter((_, itemIndex) => itemIndex !== index))}>删除章节</button></div>
              </div>
            ) : (
              <div><h3>{section.title}</h3><p>{section.purpose}</p><div className="dimension-row">{section.dimension_keys.map((key) => <span className="dimension-chip" key={key}>{key}</span>)}</div></div>
            )}
          </li>
        ))}
      </ol>
      {requestMatches && (
        <div className="outline-actions">
          <p>所有操作都绑定 Request v{request.request_version} 与当前 Outline；过期提交会被服务端拒绝。</p>
          {dirty && <p className="warn-text">存在未保存的大纲编辑。批准当前版本不会包含这些修改；请保存编辑或先重置。</p>}
          {draftIssues.length > 0 && <ul className="outline-validation" aria-live="polite">{draftIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
          {allowed.has("edit") && <div className="outline-structure-actions"><button className="button-outline" type="button" disabled={sections.length >= 12} onClick={() => setSections((current) => [...current, nextOutlineSection(current, availableDimensions)])}>添加章节</button>{dirty && <button className="button-plain" type="button" onClick={() => setSections(parsed)}>重置未保存编辑</button>}</div>}
          <div className="outline-action-buttons">
            {allowed.has("approve") && <button type="button" disabled={mutation.isPending || dirty} onClick={() => submit("approve", {})}>批准服务端当前大纲</button>}
            {allowed.has("edit") && <button className="button-outline" type="button" disabled={mutation.isPending || !dirty || draftIssues.length > 0} onClick={() => submit("edit", { sections })}>保存编辑并批准</button>}
          </div>
          {allowed.has("feedback") && <label className="feedback-field">反馈后重新生成<small>反馈只针对服务端当前大纲，不会包含上方未保存的结构化编辑。</small><textarea rows={3} maxLength={4000} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="说明希望补充、删减或重排的内容"/><button type="button" disabled={mutation.isPending || !feedback.trim()} onClick={() => submit("feedback", { feedback })}>提交反馈并暂停等待新版本</button></label>}
          {mutation.isError && <p className="error-text">{errorMessage(mutation.error)}。如 Request 已过期，请刷新后使用最新版本。</p>}
          {mutation.isSuccess && <p className="success-text">输入已持久化，Workflow 已重新排队。</p>}
        </div>
      )}
    </div>
  );
}

export default function ReviewResults({
  projectId,
  runId,
  request,
  eventSequence,
  citationsValidated,
  canSubmitHumanInput,
}: ReviewResultsProps) {
  const queryClient = useQueryClient();
  const outlineQuery = useQuery({
    queryKey: ["review-outline", projectId, runId],
    queryFn: () => apiFetch<ReviewOutput>(`/api/v1/projects/${projectId}/reviews/${runId}/outline`),
    retry: noRetryForMissing,
  });
  const matrixQuery = useQuery({
    queryKey: ["review-matrix", projectId, runId],
    queryFn: () => apiFetch<ReviewOutput>(`/api/v1/projects/${projectId}/reviews/${runId}/evidence-matrix`),
    retry: noRetryForMissing,
  });
  const sectionsQuery = useQuery({
    queryKey: ["review-sections", projectId, runId],
    queryFn: () => apiFetch<ReviewOutput[]>(`/api/v1/projects/${projectId}/reviews/${runId}/sections`),
  });
  const artifactsQuery = useQuery({
    queryKey: ["review-artifacts", projectId, runId],
    queryFn: () => apiFetch<ReviewArtifact[]>(`/api/v1/projects/${projectId}/reviews/${runId}/artifacts`),
  });

  useEffect(() => {
    if (eventSequence === 0) return;
    for (const key of ["review-outline", "review-matrix", "review-sections", "review-artifacts"]) {
      void queryClient.invalidateQueries({ queryKey: [key, projectId, runId] });
    }
  }, [eventSequence, projectId, queryClient, runId]);

  const rows = matrixRows(matrixQuery.data);
  const parsedOutline = outlineSections(outlineQuery.data);
  const availableDimensions = visibleDimensionKeys(parsedOutline, rows);
  const sectionOrder = new Map(
    parsedOutline.map((section, index) => [section.section_key, index]),
  );
  const sections = (sectionsQuery.data?.flatMap((output) => {
    const parsed = sectionResult(output);
    return parsed ? [parsed] : [];
  }) ?? []).sort(
    (left, right) =>
      (sectionOrder.get(left.section_key) ?? Number.MAX_SAFE_INTEGER) -
      (sectionOrder.get(right.section_key) ?? Number.MAX_SAFE_INTEGER),
  );
  const invalidSectionCount = (sectionsQuery.data?.length ?? 0) - sections.length;
  return (
    <div className="review-results-flow">
      <section className="section-block" aria-labelledby="outline-title">
        <div className="section-title-row"><div><p className="eyebrow">HUMAN CHECKPOINT</p><h2 id="outline-title">结构化大纲</h2></div><span className="section-count">{String(outlineSections(outlineQuery.data).length).padStart(2, "0")}</span></div>
        {outlineQuery.isPending && <p className="muted">正在读取大纲…</p>}
        {outlineQuery.isError && !isNotReady(outlineQuery.error) && <p className="error-text">{errorMessage(outlineQuery.error)}</p>}
        {!outlineQuery.isPending && (
          <OutlineWorkbench
            key={outlineQuery.data?.output_id ?? "missing"}
            projectId={projectId}
            runId={runId}
            output={outlineQuery.data}
            request={request}
            availableDimensions={availableDimensions}
            canSubmit={canSubmitHumanInput}
          />
        )}
      </section>

      <section className="section-block" aria-labelledby="matrix-title">
        <div className="section-title-row"><div><p className="eyebrow">EVIDENCE FIRST</p><h2 id="matrix-title">Evidence Matrix</h2></div><span className="section-count">{String(rows.length).padStart(2, "0")}</span></div>
        {matrixQuery.isPending && <p className="muted">正在读取 Evidence Matrix…</p>}
        {matrixQuery.isError && !isNotReady(matrixQuery.error) && <p className="error-text">{errorMessage(matrixQuery.error)}</p>}
        {!matrixQuery.isPending && (!matrixQuery.isError || isNotReady(matrixQuery.error)) && !matrixQuery.data && <div className="empty-state compact"><h3>Matrix 尚未提交</h3><p>来源全部进入就绪或稳定失败后，系统才会固定 Evidence 边界。</p></div>}
        {rows.length > 0 && <div className="matrix-table-wrap"><table className="matrix-table"><thead><tr><th>Paper</th><th>维度</th><th>结论 / 限制</th><th>Evidence</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.paper_id}:${row.dimension_key}`}><td className="mono">{row.paper_id.slice(0, 8)}</td><td>{row.dimension_key}</td><td>{row.status === "insufficient_evidence" ? <span className="badge badge-warn">证据不足</span> : <><p>{row.finding}</p>{row.limitations && <small>限制：{row.limitations}</small>}</>}</td><td><div className="evidence-chip-row">{row.evidence_ids.map((id) => <EvidenceLocator key={id} projectId={projectId} evidenceId={id}/>)}</div></td></tr>)}</tbody></table></div>}
      </section>

      <section className="section-block" aria-labelledby="sections-title">
        <div className="section-title-row"><div><p className="eyebrow">SECTION CLAIMS</p><h2 id="sections-title">章节与引用</h2></div><span className="section-count">{String(sections.length).padStart(2, "0")}</span></div>
        {sectionsQuery.isPending && <p className="muted">正在读取章节与 Evidence 绑定…</p>}
        {sectionsQuery.isError && <p className="error-text">{errorMessage(sectionsQuery.error)}</p>}
        {invalidSectionCount > 0 && <p className="error-text">{invalidSectionCount} 个 Section Output 不符合 section.v1 展示契约，已拒绝部分投影。</p>}
        {!sectionsQuery.isPending && !sectionsQuery.isError && sectionsQuery.data?.length === 0 && <div className="empty-state compact"><h3>章节尚未生成</h3><p>批准大纲后，章节会按顺序写作并经过 Citation Validator。</p></div>}
        <div className="review-section-stack">{sections.map((section, index) => <article key={section.section_key}><header><span className="outline-number">{String(index + 1).padStart(2, "0")}</span><div><h3>{section.title}</h3><p>{section.summary}</p></div><span className={`badge ${section.status === "insufficient_evidence" ? "badge-warn" : citationsValidated ? "badge-ok" : "badge-pending"}`}>{section.status === "insufficient_evidence" ? "证据不足" : citationsValidated ? "引用已校验" : "Evidence 已绑定"}</span></header><ol>{section.claims.map((claim, claimIndex) => <li key={`${section.section_key}:${claimIndex}`}><p>{claim.text}</p><div className="evidence-chip-row">{claim.evidence_ids.map((id) => <EvidenceLocator key={id} projectId={projectId} evidenceId={id}/>)}</div></li>)}</ol>{section.terminology.length > 0 && <dl className="terminology-list">{section.terminology.map((item) => <div key={item.term}><dt>{item.term}</dt><dd>{item.definition}</dd></div>)}</dl>}</article>)}</div>
      </section>

      <section className="section-block" aria-labelledby="artifacts-title">
        <div className="section-title-row"><div><p className="eyebrow">PERSISTED OUTPUTS</p><h2 id="artifacts-title">Artifacts</h2></div><span className="section-count">{String(artifactsQuery.data?.length ?? 0).padStart(2, "0")}</span></div>
        {artifactsQuery.isPending && <p className="muted">正在读取 Artifact 清单…</p>}
        {artifactsQuery.isError && <p className="error-text">{errorMessage(artifactsQuery.error)}</p>}
        {!artifactsQuery.isPending && !artifactsQuery.isError && artifactsQuery.data?.length === 0 && <div className="empty-state compact"><h3>Artifact 尚未导出</h3><p>引用校验和一致性检查完成后，六类固定文件会显示在这里。</p></div>}
        <ul className="artifact-list">{artifactsQuery.data?.map((artifact) => <li key={artifact.artifact_id}><span><strong>{artifact.artifact_type.replaceAll("_", " ")}</strong><small>{artifact.media_type} · {(artifact.size_bytes / 1024).toFixed(1)} KiB · SHA-256 {artifact.content_hash.slice(0, 12)}…</small></span><a className="button-link" href={artifactContentUrl(projectId, runId, artifact.artifact_id)} download>下载</a></li>)}</ul>
      </section>
    </div>
  );
}
