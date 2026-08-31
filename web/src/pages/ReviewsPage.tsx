import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type {
  CreateReviewResult,
  PaperListItem,
  Project,
} from "../api/types";
import PageBar from "../components/PageBar";
import { ensureReviewIntent, type ReviewIntent } from "../reviews/reviewIntent";

export default function ReviewsPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [researchQuestion, setResearchQuestion] = useState("");
  const [paperVersionIds, setPaperVersionIds] = useState<string[]>([]);
  const [autoSearchCandidates, setAutoSearchCandidates] = useState(true);
  const [intent, setIntent] = useState<ReviewIntent | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const papersQuery = useQuery({
    queryKey: ["papers", projectId],
    queryFn: () => apiFetch<PaperListItem[]>(`/api/v1/projects/${projectId}/papers`),
  });
  const createMutation = useMutation({
    mutationFn: (input: ReviewIntent) =>
      apiFetch<CreateReviewResult>(`/api/v1/projects/${projectId}/reviews`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": input.key,
        },
        body: JSON.stringify({
          research_question: input.researchQuestion,
          paper_version_ids: input.paperVersionIds,
          auto_search_candidates: input.autoSearchCandidates,
        }),
      }),
    onSuccess: (result) => {
      setIntent(null);
      setResearchQuestion("");
      setPaperVersionIds([]);
      void queryClient.invalidateQueries({ queryKey: ["reviews", projectId] });
      navigate(`/projects/${projectId}/reviews/${result.run_id}`);
    },
  });

  const project = projectQuery.data;
  const archived = Boolean(project?.archived_at);
  const selectedPaperCount = paperVersionIds.length;
  const scopeLabel = selectedPaperCount > 0
    ? `已选 ${selectedPaperCount} / 3 篇`
    : autoSearchCandidates
      ? "自动检索候选"
      : "尚未选择来源";
  const scopeDescription = selectedPaperCount >= 3
    ? "将仅使用已选的 3 篇项目论文。"
    : autoSearchCandidates
      ? selectedPaperCount > 0
        ? `将使用 ${selectedPaperCount} 篇项目论文，并检索候选补足来源。`
        : "将先检索候选，并在下载前交由你筛选。"
      : selectedPaperCount > 0
        ? `将仅使用已选的 ${selectedPaperCount} 篇项目论文。`
        : "请选择项目论文，或开启自动补充。";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = researchQuestion.trim();
    if (!question || archived || (!autoSearchCandidates && paperVersionIds.length === 0)) return;
    const nextIntent = ensureReviewIntent(
      intent,
      question,
      paperVersionIds,
      autoSearchCandidates,
      () => crypto.randomUUID(),
    );
    setIntent(nextIntent);
    createMutation.mutate(nextIntent);
  };

  if (projectQuery.isError) {
    return <div className="page-flow"><PageBar breadcrumbs={[{ label: "研究项目", to: "/" }]} title="文献研究" /><section className="notice"><p className="error-text">{errorMessage(projectQuery.error)}</p><Link to="/">返回项目</Link></section></div>;
  }

  return (
    <div className="viewport-workspace-page literature-research-page">
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "文献研究" }]}
        title={project?.name ?? "正在读取项目…"}
      />

      <div className="research-workspace review-workspace">
        <main className="conversation-main chat-create-main review-create-main" aria-labelledby="review-create-title">
          <header className="chat-heading chat-create-heading review-create-heading">
            <div>
              <h2 id="review-create-title">从项目文献出发，形成有证据的综述</h2>
              <p>先确定本次研究使用的来源，再在下方提出问题并启动工作流。</p>
            </div>
          </header>

          <form className="review-create-flow" onSubmit={submit}>
            <div className="chat-create-stage review-create-stage">
              <section className="chat-create-card review-source-card" aria-labelledby="review-source-title">
                <div className="question-starters-heading review-source-heading">
                  <div>
                    <p className="eyebrow">RESEARCH SCOPE</p>
                    <h3 id="review-source-title">选择本次研究范围</h3>
                  </div>
                  <span>{selectedPaperCount}/3 篇已选择</span>
                </div>

                <fieldset className="review-source-picker">
                  <legend>使用项目文献库中的论文 <small>可不选，最多 3 篇</small></legend>
                  <p className="review-source-hint">
                    仅显示当前项目中的论文；需要补充来源时，可先到
                    <Link to={`/projects/${projectId}?add=search`}>文献库搜索并引入</Link>。
                  </p>
                  {papersQuery.isPending && <p className="muted">正在读取项目论文…</p>}
                  {papersQuery.isError && <p className="error-text">{errorMessage(papersQuery.error)}</p>}
                  <div className="review-source-options">
                    {papersQuery.data?.map((paper) => {
                      const versionId = paper.version.version_id;
                      const checked = paperVersionIds.includes(versionId);
                      const disabled = !paper.version.parse_ready || (!checked && paperVersionIds.length >= 3);
                      return (
                        <label key={paper.paper_id} className={disabled ? "is-disabled" : undefined}>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={disabled || archived}
                            onChange={(event) => {
                              setIntent(null);
                              setPaperVersionIds((current) => {
                                const next = event.target.checked
                                  ? [...current, versionId]
                                  : current.filter((value) => value !== versionId);
                                if (next.length >= 3) setAutoSearchCandidates(false);
                                return next;
                              });
                            }}
                          />
                          <span>
                            <strong title={paper.title?.trim() || paper.version.display_filename}>{paper.title?.trim() || paper.version.display_filename}</strong>
                            <small>{paper.version.parse_ready ? "索引已就绪" : "等待解析与索引"}</small>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                  {papersQuery.data?.length === 0 && <p className="muted">项目中还没有论文，可开启自动补充后创建任务。</p>}
                </fieldset>

                <label className="review-auto-source-option">
                  <input
                    type="checkbox"
                    checked={autoSearchCandidates}
                    disabled={archived || paperVersionIds.length >= 3}
                    onChange={(event) => {
                      setIntent(null);
                      setAutoSearchCandidates(event.target.checked);
                    }}
                  />
                  <span><strong>来源不足时自动补充</strong><small>系统仅检索候选；展示摘要后由你筛选，确认前不会下载。</small></span>
                </label>
              </section>

              {!autoSearchCandidates && paperVersionIds.length === 0 && <p className="error-text review-create-feedback">请选择至少一篇已就绪论文，或开启自动补充。</p>}
              {archived && <p className="readonly-note review-create-feedback">该项目已归档。历史研究任务仍可查看，但不能创建新任务。</p>}
              {createMutation.isError && <p className="error-text review-create-feedback">{errorMessage(createMutation.error)}。修改问题前可直接重试，系统会复用本次幂等意图。</p>}
            </div>

            <div className="conversation-composer chat-create-composer review-create-composer">
              <label className="sr-only" htmlFor="research-question">研究问题</label>
              <textarea
                id="research-question"
                rows={2}
                maxLength={4000}
                value={researchQuestion}
                onChange={(event) => setResearchQuestion(event.target.value)}
                disabled={archived}
                placeholder="提出一个需要多篇文献证据回答的研究问题…"
                required
              />
              <div className="conversation-composer-toolbar">
                <div className="chat-create-composer-context review-create-composer-context">
                  <span className="context-chip">{scopeLabel}</span>
                  <small>{scopeDescription}</small>
                </div>
                <button type="submit" disabled={archived || !researchQuestion.trim() || (!autoSearchCandidates && paperVersionIds.length === 0) || createMutation.isPending}>
                  {createMutation.isPending ? "正在创建…" : "开始研究"}<span aria-hidden="true">→</span>
                </button>
              </div>
            </div>
          </form>
        </main>
      </div>
    </div>
  );
}
