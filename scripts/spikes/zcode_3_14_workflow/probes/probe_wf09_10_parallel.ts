// WF-09 / WF-10 parallel fan-out + disjoint writes probe — NON-PRODUCTION.
// Three children run in one Promise.all stage: A and B write disjoint
// fixture files, C only computes. Each child reports epoch timestamps from
// `date +%s.%N` around its work so the script can compute pairwise overlap.
// A fourth read-only child in a second stage reads the two files back so the
// final on-disk state is evidenced by a child (the script never reads the
// git-ignored evidence tree itself).

interface ChildTiming {
  name: string;
  start_epoch: string;
  end_epoch: string;
  work: string;
  git_status: string;
}

interface Readback {
  a_content: string;
  b_content: string;
  listing: string;
}

phase("WF-09 三子代理并行：计时 + 两个不相交文件写入");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf09";

const common =
  "开始任务前先用 Bash 运行 date +%s.%N 记录为 start_epoch；任务完成后再次运行记录为 end_epoch。" +
  "然后用 Bash 运行 git status --porcelain 记录为 git_status。" +
  '返回 JSON：{"name": "<你的名字>", "start_epoch": "<原样>", "end_epoch": "<原样>", ' +
  '"work": "<你的任务结果一句话>", "git_status": "<原样输出或空串>"}。' +
  "除任务要求外不要写其他文件。只返回 JSON。";

const a = agent("wf09-a", "你是探针A，负责写文件 a.txt。");
const b = agent("wf09-b", "你是探针B，负责写文件 b.txt。");
const c = agent("wf09-c", "你是探针C，只做计算，不写任何文件。");

const [ra, rb, rc] = await Promise.all([
  a.ask<ChildTiming>(
    "任务：用 Bash 运行 mkdir -p " + dir + " ，再用 Write 工具把一行 DISJOINT-A 写入 " +
      dir + "/a.txt 。" + common,
  ),
  b.ask<ChildTiming>(
    "任务：用 Bash 运行 mkdir -p " + dir + " ，再用 Write 工具把一行 DISJOINT-B 写入 " +
      dir + "/b.txt 。" + common,
  ),
  c.ask<ChildTiming>("任务：计算 17 乘以 23 的值，写入 work 字段。" + common),
]);

function epoch(value: string): number {
  const n = Number(String(value).trim());
  return Number.isFinite(n) ? n : 0;
}

const all = [ra, rb, rc];
const overlapPairs: string[] = [];
for (let i = 0; i < all.length; i++) {
  for (let j = i + 1; j < all.length; j++) {
    const x = all[i];
    const y = all[j];
    const overlaps = epoch(x.start_epoch) < epoch(y.end_epoch) &&
      epoch(y.start_epoch) < epoch(x.end_epoch);
    if (overlaps) {
      overlapPairs.push(x.name + "||" + y.name);
    }
  }
}

phase("WF-10 只读子代理回读两个不相交文件的最终落盘状态");

const reader = agent("wf09-reader", "你是只读回读探针。");
const readback = await reader.ask<Readback>(
  "任务：用 Read 工具分别读取 " + dir + "/a.txt 和 " + dir + "/b.txt 的当前内容；" +
    "再用 Bash 运行 ls -l " + dir + " 记录清单。" +
    '返回 JSON：{"a_content": "<原样>", "b_content": "<原样>", "listing": "<原样>"}。只返回 JSON。',
);

return {
  workflow: "wf09_10_parallel",
  case: "WF-09+WF-10",
  children: all,
  overlap_pairs_observed: overlapPairs,
  overlap_pairs_text: overlapPairs.length > 0 ? overlapPairs.join(", ") : "none",
  disjoint_final_state: readback,
};
