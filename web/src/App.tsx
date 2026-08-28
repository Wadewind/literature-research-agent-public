import { useEffect, type ReactNode } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import AppSidebar from "./components/AppSidebar";
import PageBar from "./components/PageBar";
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
    <AppFrame navigation={<AppSidebar />}>
      <ScrollToTop />
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
    </AppFrame>
  );
}

export const MAIN_CONTENT_ID = "main-content";

export function AppFrame({
  navigation,
  children,
}: {
  navigation: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <a className="skip-link" href={`#${MAIN_CONTENT_ID}`}>跳到主内容</a>
      {navigation}
      <main id={MAIN_CONTENT_ID} className="app-main" tabIndex={-1}>
        {children}
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
    <div className="stack">
      <PageBar title="页面不存在" />
      <section className="panel">
        <p>
          你访问的地址没有对应的页面。返回 <Link to="/">Project 列表</Link>。
        </p>
      </section>
    </div>
  );
}
