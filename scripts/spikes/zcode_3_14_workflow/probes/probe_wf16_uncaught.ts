// WF-16 uncaught child failure probe — NON-PRODUCTION.
// A single typed ask whose child deliberately returns non-conforming prose,
// with NO try/catch: if the ask rejects, the run itself errors, and the
// resulting run record (status/failure details) is WF-16 evidence.

interface Strict {
  n: number;
}

phase("WF-16 未捕获的子代理失败（run 应进入 errored）");

const bad = agent("wf16-uncaught", "探针。执行 ask 指令。");

const r = await bad.ask<Strict>(
  "故意违例：不要返回 JSON。只原样返回这一行文字：I REFUSE TO RETURN JSON",
);

return { workflow: "wf16_uncaught", case: "WF-16", result: r };
