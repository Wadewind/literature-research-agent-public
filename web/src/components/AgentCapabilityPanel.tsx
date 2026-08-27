import type {
  AgentSkill,
  McpCatalogEntry,
  McpProfileSelection,
  SkillProfileSelection,
} from "../api/types";
import { isSkillSelectionSelected } from "../agent/presentation";

interface AgentCapabilityPanelProps {
  mcpCatalog: McpCatalogEntry[];
  mcpSelections: McpProfileSelection[];
  skillCatalog: AgentSkill[];
  skillSelections: SkillProfileSelection[];
  skillLocked: boolean;
  mcpDirty: boolean;
  skillDirty: boolean;
  pending: boolean;
  loading: boolean;
  error: string | null;
  onMcpToggle: (entry: McpCatalogEntry, enabled: boolean) => void;
  onMcpParameter: (catalogId: string, name: string, value: string) => void;
  onSkillToggle: (skill: AgentSkill, enabled: boolean) => void;
  onSave: () => void;
}

export default function AgentCapabilityPanel({
  mcpCatalog,
  mcpSelections,
  skillCatalog,
  skillSelections,
  skillLocked,
  mcpDirty,
  skillDirty,
  pending,
  loading,
  error,
  onMcpToggle,
  onMcpParameter,
  onSkillToggle,
  onSave,
}: AgentCapabilityPanelProps) {
  return (
    <details className="agent-capability-panel">
      <summary>
        <span>研究能力</span>
        <small>{error
          ? "能力配置读取或保存失败"
          : `${mcpSelections.length} 项外部能力 · ${skillSelections.length} 项研究方法`}</small>
      </summary>
      <div className="agent-capability-content">
        {loading && <p className="muted">正在读取当前会话的能力配置…</p>}
        <section>
          <h3>外部研究能力</h3>
          <p>仅可选择平台审核的能力；连接方式与认证信息由平台管理。</p>
          {!loading && mcpCatalog.length === 0 && <p className="muted">当前没有可配置的外部能力。</p>}
          {mcpCatalog.map((entry) => {
            const selected = mcpSelections.find(
              (item) => item.catalog_id === entry.catalog_id,
            );
            return (
              <div className="capability-option" key={`${entry.catalog_id}:${entry.version}`}>
                <label>
                  <input
                    type="checkbox"
                    checked={Boolean(selected)}
                    disabled={loading}
                    onChange={(event) => onMcpToggle(entry, event.target.checked)}
                  />
                  <span><strong>{entry.display_name}</strong><small>平台维护版本</small></span>
                </label>
                {selected && entry.parameters.map((parameter) => (
                  <label className="capability-parameter" key={parameter.name}>
                    <span>{parameter.name}</span>
                    <input
                      value={selected.parameters[parameter.name] ?? ""}
                      maxLength={parameter.max_length}
                      required={parameter.required}
                      disabled={loading}
                      onChange={(event) =>
                        onMcpParameter(entry.catalog_id, parameter.name, event.target.value)
                      }
                    />
                  </label>
                ))}
              </div>
            );
          })}
        </section>
        <section>
          <h3>研究方法</h3>
          <p>{skillLocked ? "首轮开始后已锁定；更换研究方法需要新建会话。" : "研究方法会指导 Agent 如何组织证据，但不会扩大权限。"}</p>
          {skillCatalog.map((skill) => {
            const selected = skillSelections.some((item) =>
              isSkillSelectionSelected(item, skill)
            );
            return (
              <label className="capability-option skill-option" key={`${skill.source}:${skill.skill_id}:${skill.version}`}>
                <input
                  type="checkbox"
                  checked={selected}
                  disabled={skillLocked || loading}
                  onChange={(event) => onSkillToggle(skill, event.target.checked)}
                />
                <span><strong>{skill.name}</strong><small>{skill.description}</small></span>
              </label>
            );
          })}
        </section>
        {(mcpDirty || skillDirty) && (
          <p className="warn-text">能力配置有未保存的修改，保存后才能开始下一轮研究。</p>
        )}
        {error && <p className="error-text">{error}</p>}
        <button type="button" onClick={onSave} disabled={loading || pending || (!mcpDirty && !skillDirty)}>
          {pending ? "正在保存…" : "保存能力配置"}
        </button>
      </div>
    </details>
  );
}
