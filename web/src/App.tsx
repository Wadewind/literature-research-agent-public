import { Link, Route, Routes } from "react-router-dom";

import DocumentPage from "./pages/DocumentPage";
import LibraryPage from "./pages/LibraryPage";
import ProjectsPage from "./pages/ProjectsPage";
import RunDetailPage from "./pages/RunDetailPage";

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title">
          文献综述 Agent
        </Link>
        <span className="app-phase">Phase 1 · 文献库与导入</span>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
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
