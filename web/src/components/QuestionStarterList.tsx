import {
  CHAT_QUESTION_TEMPLATES,
  type ChatQuestionTemplateId,
} from "../conversations/questionTemplates";

interface QuestionStarterListProps {
  selectedId: ChatQuestionTemplateId | null;
  disabled: boolean;
  onSelect: (templateId: ChatQuestionTemplateId) => void;
}

export default function QuestionStarterList({
  selectedId,
  disabled,
  onSelect,
}: QuestionStarterListProps) {
  return (
    <section className="question-starters" aria-labelledby="question-starters-title">
      <div className="question-starters-heading">
        <div>
          <p className="eyebrow">QUESTION STARTERS</p>
          <h3 id="question-starters-title">选择一个研究切入点</h3>
        </div>
        <span>点击后继续确认文献范围</span>
      </div>
      <div className="question-starter-grid">
        {CHAT_QUESTION_TEMPLATES.map((template) => (
          <button
            key={template.id}
            type="button"
            className={`question-starter${selectedId === template.id ? " is-selected" : ""}`}
            aria-pressed={selectedId === template.id}
            disabled={disabled}
            onClick={() => onSelect(template.id)}
          >
            <span className="question-starter-meta">
              <span>{template.label}</span>
              <small>{template.code}</small>
            </span>
            <strong>{template.question}</strong>
            <span className="question-starter-arrow" aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </section>
  );
}
