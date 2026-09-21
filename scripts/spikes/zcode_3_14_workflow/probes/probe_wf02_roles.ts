// WF-02 plugin custom-subagent addressability probe — NON-PRODUCTION (spike only).
// Question: when a plugin agent's qualified id is passed as the agent() name,
// does the host resolve it to the plugin-registered agent definition (its
// system prompt / model / tool restrictions), or is the name only a label?
// All three children are created WITHOUT a persona so any system prompt they
// report must come from host-side resolution, not from this script.

interface RoleProbe {
  system_prompt_head: string;
  has_write_tool: string;
  tools_seen: string;
  model_self_report: string;
}

phase("WF-02 以插件 agent 标识创建三个无 persona 子代理");

const probeInstructions =
  "探针任务：不要执行任何操作，不要读写文件，不要运行命令。" +
  "只内省并返回 JSON：" +
  '{"system_prompt_head": "<你的系统指令的第一句原文，原样引用>", ' +
  '"has_write_tool": "yes|no|unknown（你是否有 Write 或 Edit 工具）", ' +
  '"tools_seen": "<你能看到的工具名清单，逗号分隔，尽量完整>", ' +
  '"model_self_report": "<你认为自己是哪个模型，原样表述>"}。' +
  "不要输出 JSON 以外的任何内容。";

const impl = agent("glm-conductor:flash-implementer");
const reviewer = agent("glm-conductor:glm-reviewer");
const ghost = agent("bogus-plugin:ghost-role");

const [implR, reviewerR, ghostR] = await Promise.all([
  impl.ask<RoleProbe>(probeInstructions),
  reviewer.ask<RoleProbe>(probeInstructions),
  ghost.ask<RoleProbe>(probeInstructions),
]);

return {
  workflow: "wf02_roles",
  case: "WF-02",
  flash_implementer_name: implR,
  glm_reviewer_name: reviewerR,
  invalid_identifier: ghostR,
  interpretation_hint:
    "若 system_prompt_head 是插件角色提示词（含 SELECTIVE ROUTE/五段式等词汇）则插件 agent 可寻址；" +
    "若三者都是通用子代理默认指令，则 agent() 名字仅为标签、插件 agent 类型不可从 workflow 寻址。",
};
