import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

export const PAGE_BAR_TITLE_MAX_PX = 20;

export interface PageBarBreadcrumb {
  label: string;
  to?: string;
}

interface PageBarProps {
  breadcrumbs?: readonly PageBarBreadcrumb[];
  title: ReactNode;
  actions?: ReactNode;
}

const PAGE_BAR_STYLE = {
  "--page-bar-title-size": `${PAGE_BAR_TITLE_MAX_PX}px`,
} as CSSProperties;

export default function PageBar({ breadcrumbs = [], title, actions }: PageBarProps) {
  return (
    <header className="page-bar" style={PAGE_BAR_STYLE}>
      <div className="page-bar-identity">
        {breadcrumbs.length > 0 ? (
          <nav className="page-bar-breadcrumbs" aria-label="面包屑">
            <ol>
              {breadcrumbs.map((breadcrumb, index) => (
                <li key={`${breadcrumb.to ?? "current"}:${breadcrumb.label}`}>
                  {index > 0 ? <span className="page-bar-separator" aria-hidden="true">/</span> : null}
                  {breadcrumb.to ? <Link to={breadcrumb.to}>{breadcrumb.label}</Link> : <span>{breadcrumb.label}</span>}
                </li>
              ))}
            </ol>
          </nav>
        ) : null}
        <h1 className="page-bar-title" title={typeof title === "string" ? title : undefined}>{title}</h1>
      </div>
      {actions ? <div className="page-bar-actions" role="group" aria-label="页面操作">{actions}</div> : null}
    </header>
  );
}
