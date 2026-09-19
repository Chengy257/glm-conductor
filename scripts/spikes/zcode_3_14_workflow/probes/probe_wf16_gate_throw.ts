// WF-16 script-level gate failure probe — NON-PRODUCTION.
// The child succeeds deterministically; the script itself then throws
// (simulating a conductor gate rejecting a child result). This yields an
// OBSERVED run-errored status with failure details for the results doc.

interface GateInput {
  ok: boolean;
}

phase("WF-16 子代理成功后脚本门禁故意 throw（run 应 errored）");

const child = agent("wf16-gate", "探针。执行 ask 指令。");

const r = await child.ask<GateInput>(
  '任务：返回 JSON {"ok": true}。只返回 JSON。',
);

if (!r.ok) {
  throw new Error("wf16 gate: child reported not-ok");
}

throw new Error("wf16-deliberate-gate-failure (spike): gate rejected the child result");
