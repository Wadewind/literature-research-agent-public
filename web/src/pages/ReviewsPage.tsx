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
    <div className="page-flow literature-research-page">
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "文献研究" }]}
        title={project?.name ?? "正在读取项目…"}
      />

      <section className="review-create-workspace" aria-labelledby="review-create-title">
        <header className="review-create-heading">
          <p className="project-library-kicker">新建文献研究</p>
          <h2 id="review-create-title">从一个明确的问题开始</h2>
          <p>选择项目文献作为研究范围；不足三篇时，可让系统检索候选并在下载前交由你筛选。</p>
        </header>
        <form className="review-create-form" onSubmit={submit}>
          <div className="review-question-field">
            <label htmlFor="research-question">研究问题</label>
            <textarea
              id="research-question"
              rows={4}
              maxLength={4000}
              value={researchQuestion}
              onChange={(event) => setResearchQuestion(event.target.value)}
              disabled={archived}
              placeholder="例如：强化学习方法在动态路径规划中有哪些优势与局限？"
              required
            />
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
                      <strong>{paper.title?.trim() || paper.version.display_filename}</strong>
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
          <div className="review-create-footer">
            <small>将生成 Evidence Matrix、结构化大纲和简要综述；任务可恢复、可取消。</small>
            <button type="submit" disabled={archived || !researchQuestion.trim() || (!autoSearchCandidates && paperVersionIds.length === 0) || createMutation.isPending}>
              {createMutation.isPending ? "正在创建…" : "开始文献研究"}<span aria-hidden="true">→</span>
            </button>
          </div>
          {!autoSearchCandidates && paperVersionIds.length === 0 && <p className="error-text">请选择至少一篇已就绪论文，或开启自动补充。</p>}
          {archived && <p className="readonly-note">该项目已归档。历史研究任务仍可查看，但不能创建新任务。</p>}
          {createMutation.isError && <p className="error-text">{errorMessage(createMutation.error)}。修改问题前可直接重试，系统会复用本次幂等意图。</p>}
        </form>
      </section>
    </div>
  );
}
