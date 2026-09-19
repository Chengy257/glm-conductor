// WF-03 model binding self-report probe — NON-PRODUCTION (spike only).
// This copy is submitted with NO subagent_model (children inherit the session
// model by documented facade rule). A second submission attempts
// subagent_model = "account:bigmodel-individual-coding-plan/GLM-5.3-Flash"
// (the plugin agent frontmatter value) and is expected to be rejected at
// submission validation, because that provider/model is not configured on
// this host. Child self-reports are corroboration, not proof; run-record
// fields and ListModels output are recorded separately in the results doc.

interface ModelProbe {
  role_label: string;
  model_self_report: string;
  provider_hint: string;
  thought_level_self_report: string;
}

phase("WF-03 两个角色子代理自报模型身份");

const common =
  "只内省并返回 JSON：" +
  '{"model_self_report": "<你认为自己是哪个模型，原样表述>", ' +
  '"provider_hint": "<若你知道自己的 provider/账户标识则报告，否则写 unknown>", ' +
  '"thought_level_self_report": "<若你能感知推理力度级别则报告，否则写 unknown>"}。' +
  "不要读写文件，不要运行命令。不要输出 JSON 以外的任何内容。";

const impl = agent(
  "wf03-flash-implementer",
  "你是实施者角色探针（对应 flash-implementer 语义）。执行 ask 指令。",
);
const reviewer = agent(
  "wf03-flagship-reviewer",
  "你是审阅者角色探针（对应 flagship reviewer 语义）。执行 ask 指令。",
);

const [implR, reviewerR] = await Promise.all([
  impl.ask<ModelProbe>('你是实施者角色。' + common),
  reviewer.ask<ModelProbe>('你是审阅者角色。' + common),
]);

return {
  workflow: "wf03_model",
  case: "WF-03",
  submission_variant: "default_subagent_model_omitted",
  implementer_role: implR,
  reviewer_role: reviewerR,
};
