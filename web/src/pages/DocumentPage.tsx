/** 文档结构预览页：Element 按阅读顺序渲染，点击定位到来源 PDF 页码。 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiFetch, errorMessage } from "../api/client";
import type { DocElement, DocumentOverview } from "../api/types";

const ELEMENT_TYPE_LABELS: Record<string, string> = {
  title: "标题",
  section_heading: "章节标题",
  paragraph: "段落",
  list_item: "列表项",
  table: "表格",
  formula: "公式",
  figure: "图片",
  caption: "题注",
  page_header: "页眉",
  page_footer: "页脚",
};

function firstPage(element: DocElement): number | null {
  return element.locations.length > 0 ? element.locations[0].page : null;
}

/** 按 Element 类型渲染内容主体。 */
function ElementBody({ element }: { element: DocElement }) {
  if (element.element_type === "table") {
    const cells = element.payload.cells;
    if (Array.isArray(cells)) {
      return (
        <table className="element-table">
          <tbody>
            {(cells as unknown[][]).map((row, rowIndex) => (
              <tr key={rowIndex}>
                {Array.isArray(row) &&
                  row.map((cell, cellIndex) => <td key={cellIndex}>{String(cell)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
    return <p className="muted">（表格结构缺失）</p>;
  }
  if (element.element_type === "figure") {
    return <p className="muted">［图片］{element.text ?? ""}</p>;
  }
  if (element.element_type === "formula") {
    const latex = element.payload.latex;
    return <code className="element-formula">{typeof latex === "string" ? latex : element.text}</code>;
  }
  if (element.element_type === "title") {
    return <h3 className="element-title">{element.text}</h3>;
  }
  if (element.element_type === "section_heading") {
    return (
      <h4 className="element-heading">
        {element.section_path && <span className="mono muted">{element.section_path} </span>}
        {element.text}
      </h4>
    );
  }
  return <p>{element.text ?? <span className="muted">（无文本内容）</span>}</p>;
}

export default function DocumentPage() {
  const { projectId = "", versionId = "" } = useParams();
  const [sectionFilter, setSectionFilter] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const documentQuery = useQuery({
    queryKey: ["document", projectId, versionId],
    queryFn: () =>
      apiFetch<DocumentOverview>(
        `/api/v1/projects/${projectId}/paper-versions/${versionId}/document`,
      ),
    retry: (failureCount, error) => {
      // 尚未解析完成（document_not_ready）不重试，由用户稍后刷新
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 1;
    },
  });

  const elementsQuery = useQuery({
    queryKey: ["elements", projectId, versionId, sectionFilter],
    queryFn: () => {
      const params = new URLSearchParams({ limit: "200" });
      if (sectionFilter) params.set("section", sectionFilter);
      return apiFetch<DocElement[]>(
        `/api/v1/projects/${projectId}/paper-versions/${versionId}/elements?${params}`,
      );
    },
    enabled: documentQuery.isSuccess,
  });

  const selected = useMemo(
    () => elementsQuery.data?.find((e) => e.element_id === selectedId) ?? null,
    [elementsQuery.data, selectedId],
  );
  const selectedPage = selected ? firstPage(selected) : null;
  const fileUrl = `/api/v1/projects/${projectId}/paper-versions/${versionId}/file`;

  if (documentQuery.isError) {
    const notReady =
      documentQuery.error instanceof ApiError &&
      documentQuery.error.detail === "document_not_ready";
    return (
      <section className="panel">
        {notReady ? (
          <>
            <h1>解析尚未完成</h1>
            <p className="muted">该版本还没有可用的解析结果，请稍后在文献库中刷新。</p>
          </>
        ) : (
          <p className="error-text">{errorMessage(documentQuery.error)}</p>
        )}
        <Link to={`/projects/${projectId}`}>返回文献库</Link>
      </section>
    );
  }

  const overview = documentQuery.data;

  return (
    <div className="stack">
      <section className="panel">
        <p className="breadcrumb">
          <Link to={`/projects/${projectId}`}>文献库</Link> / 文档结构
        </p>
        {overview && (
          <div className="run-meta">
            <span className="muted">
              {overview.parser_name} {overview.parser_version} · {overview.element_count} 个
              Element
            </span>
            {overview.degraded && <span className="badge badge-warn">降级解析</span>}
          </div>
        )}
        {overview && overview.warnings.length > 0 && (
          <p className="warn-text">警告：{overview.warnings.join("、")}</p>
        )}
        {overview && overview.sections.length > 0 && (
          <label className="filter-row">
            章节过滤{" "}
            <select
              value={sectionFilter}
              onChange={(e) => {
                setSectionFilter(e.target.value);
                setSelectedId(null);
              }}
            >
              <option value="">全部章节</option>
              {overview.sections.map((section) => (
                <option key={section.section_path} value={section.section_path}>
                  {section.section_path} {section.title}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      <div className="document-pane">
        <section className="panel element-list">
          {elementsQuery.isPending && <p className="muted">加载中…</p>}
          {elementsQuery.isError && (
            <p className="error-text">{errorMessage(elementsQuery.error)}</p>
          )}
          {elementsQuery.data?.map((element) => {
            const muted =
              element.element_type === "page_header" || element.element_type === "page_footer";
            const page = firstPage(element);
            return (
              <article
                key={element.element_id}
                className={`element ${muted ? "element-muted" : ""} ${
                  element.element_id === selectedId ? "element-selected" : ""
                }`}
                onClick={() => setSelectedId(element.element_id)}
              >
                <header className="element-header">
                  <span className="badge badge-muted">
                    {ELEMENT_TYPE_LABELS[element.element_type] ?? element.element_type}
                  </span>
                  {page !== null && <span className="mono muted">p.{page}</span>}
                  {element.warnings.length > 0 && (
                    <span className="badge badge-warn">{element.warnings.join("、")}</span>
                  )}
                </header>
                <ElementBody element={element} />
              </article>
            );
          })}
        </section>

        <section className="panel pdf-pane">
          <h2>来源 PDF</h2>
          {selected && selectedPage !== null ? (
            <p className="muted">
              已定位到第 {selectedPage} 页（Element #{selected.sequence}）
            </p>
          ) : (
            <p className="muted">点击左侧 Element 定位到来源页码。</p>
          )}
          {/* key 强制按页码重建 iframe，确保原生 PDF 查看器跳转 */}
          <iframe
            key={selectedPage ?? "cover"}
            title="PDF 来源预览"
            className="pdf-frame"
            src={selectedPage !== null ? `${fileUrl}#page=${selectedPage}` : fileUrl}
          />
        </section>
      </div>
    </div>
  );
}
