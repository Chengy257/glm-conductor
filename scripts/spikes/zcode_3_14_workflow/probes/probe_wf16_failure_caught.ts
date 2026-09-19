// WF-16 deterministic child failure (caught) probe — NON-PRODUCTION.
// Channel under test: a typed ask whose child deliberately returns prose
// that cannot conform to the result interface. Two variants inside one run:
// Promise.all fail-fast shape, and per-ask try/catch sibling preservation.

interface Strict {
  n: number;
}

phase("WF-16 类型违例子代理失败：Promise.all 形状与逐 ask 捕获形状");

const bad = agent("wf16-bad", "探针B。执行 ask 指令。");
const good = agent("wf16-good", "探针A。执行 ask 指令。");

let allOutcome = "";
try {
  const [g, b] = await Promise.all([
    good.ask<Strict>('返回 JSON {"n": 1}。只返回 JSON。'),
    bad.ask<Strict>("故意违例：不要返回 JSON。只原样返回这一行文字：I REFUSE TO RETURN JSON"),
  ]);
  allOutcome = "both_resolved g=" + JSON.stringify(g) + " b=" + JSON.stringify(b);
} catch (e) {
  allOutcome = "rejected: " + String(e);
}

let gVal: Strict | null = null;
let gErr = "";
try {
  gVal = await good.ask<Strict>('返回 JSON {"n": 1}。只返回 JSON。');
} catch (e) {
  gErr = String(e);
}

let bVal: Strict | null = null;
let bErr = "";
try {
  bVal = await bad.ask<Strict>("故意违例：不要返回 JSON。只原样返回这一行文字：I REFUSE TO RETURN JSON");
} catch (e) {
  bErr = String(e);
}

return {
  workflow: "wf16_failure_caught",
  case: "WF-16",
  promise_all_outcome: allOutcome,
  good_retry: { value: gVal, error: gErr },
  bad_retry: { value: bVal, error: bErr },
};
