import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";

import DocumentPage from "./pages/DocumentPage";
import LibraryPage from "./pages/LibraryPage";
import PersonalLibraryPage from "./pages/PersonalLibraryPage";
import ProjectsPage from "./pages/ProjectsPage";
import RunDetailPage from "./pages/RunDetailPage";

export default function App() {
  return (
    <div className="app-shell">
      <ScrollToTop />
      <header className="app-header">
        <Link to="/" className="brand" aria-label="返回项目首页">
          <span className="brand-mark" aria-hidden="true">L·A</span>
          <span><strong>Literature Atlas</strong><small>文献综述 Agent</small></span>
        </Link>
        <nav className="primary-nav" aria-label="主导航">
          <NavLink to="/" end>项目</NavLink>
          <NavLink to="/library">个人文献库</NavLink>
        </nav>
        <span className="phase-chip">PHASE 01</span>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/library" element={<PersonalLibraryPage />} />
          <Route path="/projects/:projectId" element={<LibraryPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route
            path="/projects/:projectId/versions/:versionId/document"
            element={<DocumentPage />}
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}

function ScrollToTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [pathname]);

  return null;
}

function NotFound() {
  return (
    <section className="panel">
      <h1>页面不存在</h1>
      <p>
        你访问的地址没有对应的页面。返回 <Link to="/">Project 列表</Link>。
      </p>
    </section>
  );
}
