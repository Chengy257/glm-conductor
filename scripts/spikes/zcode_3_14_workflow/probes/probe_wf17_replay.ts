// WF-17 completed-step replay vs live re-dispatch probe — NON-PRODUCTION.
// Phase A child completes quickly and writes a.txt. Phase B child sleeps 60s
// then writes b.txt. The main session stops the run ~25s in (B unfinished),
// then resumes: A's completed ask must replay from the journal WITHOUT
// re-executing (a.txt mtime unchanged), B re-dispatches live (b.txt fresh).

interface FileProof {
  name: string;
  stamp: string;
}

phase("WF-17 阶段A：快速完成并落盘");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf17";

const a = agent("wf17-a", "阶段A探针。执行 ask 指令。");
const ra = await a.ask<FileProof>(
  "任务：用 Bash 运行 mkdir -p " + dir + "；用 Write 工具把一行 a-WROTE-<date +%s.%N 输出> 写入 " +
    dir + "/a.txt 。" +
    '返回 JSON：{"name": "a", "stamp": "<你写入文件的时间戳>"}。只返回 JSON。',
);

phase("WF-17 阶段B：60 秒休眠（供中途取消）");

const b = agent("wf17-b", "阶段B探针。执行 ask 指令。");
const rb = await b.ask<FileProof>(
  "任务：先用 Bash 运行 date +%s.%N 记录开始；用 Bash 运行 sleep 60；用 Bash 运行 mkdir -p " +
    dir + "；用 Write 工具把一行 b-WROTE-<date +%s.%N 输出> 写入 " + dir + "/b.txt 。" +
    '返回 JSON：{"name": "b", "stamp": "<你写入文件的时间戳>"}。只返回 JSON。',
);

return { workflow: "wf17_replay", case: "WF-17", a: ra, b: rb };
