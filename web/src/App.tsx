import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";

import DocumentPage from "./pages/DocumentPage";
import AgentPage from "./pages/AgentPage";
import ConversationPage from "./pages/ConversationPage";
import ChatPage from "./pages/ChatPage";
import LibraryPage from "./pages/LibraryPage";
import PersonalLibraryPage from "./pages/PersonalLibraryPage";
import ProjectsPage from "./pages/ProjectsPage";
import RunDetailPage from "./pages/RunDetailPage";
import ReviewsPage from "./pages/ReviewsPage";
import ReviewDetailPage from "./pages/ReviewDetailPage";

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
        <span className="phase-chip">RESEARCH AGENT · SPIKE</span>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/library" element={<PersonalLibraryPage />} />
          <Route path="/projects/:projectId" element={<LibraryPage />} />
          <Route path="/projects/:projectId/chat" element={<ChatPage />} />
          <Route
            path="/projects/:projectId/chat/:conversationId"
            element={<ConversationPage />}
          />
          <Route path="/projects/:projectId/agent" element={<AgentPage />} />
          <Route path="/projects/:projectId/agent/:sessionId" element={<AgentPage />} />
          <Route path="/projects/:projectId/reviews" element={<ReviewsPage />} />
          <Route
            path="/projects/:projectId/reviews/:runId"
            element={<ReviewDetailPage />}
          />
          <Route
            path="/projects/:projectId/conversations/:conversationId"
            element={<ConversationPage />}
          />
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
  const { pathname, hash } = useLocation();

  useEffect(() => {
    if (hash) {
      document.getElementById(hash.slice(1))?.scrollIntoView();
      return;
    }
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [hash, pathname]);

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
