// 前端纯逻辑测试：五段胶片条落点、上传四段报错、三段式直传的失败分支。
//
// 仓库没有 jest / vitest，这里用 Node 自带的 `node:test`，TS 源码经 jiti
// （tailwindcss 已经带进来的依赖）现场转译加载——不为几十行断言引一套测试框架。
// 被测文件都不依赖 `@/` 路径别名与 React。
//
// 跑法：cd apps/web && node --test tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";

const jiti = createJiti(fileURLToPath(import.meta.url));
const links = await jiti.import("../lib/freeflow/stage-links.ts");
const stage = await jiti.import("../lib/upload-stage.ts");
const api = await jiti.import("../lib/api.ts");

const BASE = "/freeflow/projects/p1";

test("五段都有落点，锁定的也有", () => {
  assert.deepEqual(
    ["story", "script", "assets", "storyboard", "generation"].map((k) => links.stageHref(k, BASE)),
    [
      `${BASE}/story#plot-index`,
      `${BASE}/story#screenplay`,
      `${BASE}/characters`,
      `${BASE}/storyboard`,
      `${BASE}/tasks`,
    ],
  );
});

test("正在查看的段按路由与 hash 算，与生产阶段无关", () => {
  assert.equal(links.viewingStage(`${BASE}/story`, ""), "story");
  assert.equal(links.viewingStage(`${BASE}/story`, "#plot-index"), "story");
  assert.equal(links.viewingStage(`${BASE}/story`, "#screenplay"), "script");
  assert.equal(links.viewingStage(`${BASE}/characters`, ""), "assets");
  assert.equal(links.viewingStage(`${BASE}/scenes/`, ""), "assets");
  assert.equal(links.viewingStage(`${BASE}/storyboard`, ""), "storyboard");
  assert.equal(links.viewingStage(`${BASE}/tasks`, ""), "generation");
  assert.equal(links.viewingStage(`${BASE}/overview`, ""), null);
  assert.equal(links.viewingStage(`${BASE}/settings`, ""), null);
});

test("每一段报错都写明是哪一步，保留后端文案", () => {
  assert.match(
    stage.uploadStageMessage({ stage: "create", backendMessage: "存储空间已满" }),
    /^创建上传失败：存储空间已满$/,
  );
  assert.match(stage.uploadStageMessage({ stage: "transfer", status: 403 }), /^文件传输失败.*HTTP 403/);
  assert.match(stage.uploadStageMessage({ stage: "transfer", status: 0 }), /^文件传输失败：连不上对象存储/);
  assert.match(stage.uploadStageMessage({ stage: "complete" }), /^完成登记失败/);
  assert.match(stage.uploadStageMessage({ stage: "bind", backendMessage: "角色不存在" }), /^设为基准图失败：角色不存在$/);
});

test("报错里不出现上传地址或 Key", () => {
  const msg = stage.uploadStageMessage({
    stage: "complete",
    backendMessage: "HEAD 失败 https://minio:9000/bucket/x?X-Amz-Signature=abc sk-secret123456",
  });
  assert.ok(!msg.includes("http"), msg);
  assert.ok(!msg.includes("Signature"), msg);
  assert.ok(!msg.includes("sk-"), msg);
});

// ---------------------------------------------------------------- assets.upload 三段式

const TICKET = {
  asset: { id: "a1" },
  upload_url: "http://storage.invalid/bucket/a1?X-Amz-Signature=SECRET",
  expires_at: "2099-01-01T00:00:00Z",
};

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function runUpload(handler) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? "GET" });
    return handler(String(url), init);
  };
  try {
    const file = new File([new Uint8Array([137, 80, 78, 71])], "a.png", { type: "image/png" });
    return { result: await api.assets.upload(file, "p1"), calls };
  } catch (error) {
    return { error, calls };
  } finally {
    globalThis.fetch = original;
  }
}

test("三段都成功：返回资产 id，PUT 的 Content-Type 与文件一致", async () => {
  const { result, calls } = await runUpload((url, init) => {
    if (url.endsWith("/assets/upload-url")) return json(201, TICKET);
    if (url === TICKET.upload_url) {
      assert.equal(init.headers["Content-Type"], "image/png");
      return new Response(null, { status: 200 });
    }
    if (url.endsWith("/assets/a1/complete")) return json(200, { id: "a1", status: "ready" });
    throw new Error(`unexpected ${url}`);
  });
  assert.equal(result, "a1");
  assert.deepEqual(calls.map((c) => c.method), ["POST", "PUT", "POST"]);
});

test("原生 PUT 失败：报文件传输失败 + 状态码，不带上传地址", async () => {
  const { error, calls } = await runUpload((url) => {
    if (url.endsWith("/assets/upload-url")) return json(201, TICKET);
    if (url === TICKET.upload_url) return new Response("denied", { status: 403 });
    throw new Error("complete 不该被调用");
  });
  assert.equal(error.error.code, "asset.upload.put_failed");
  assert.match(error.error.user_message, /^文件传输失败.*403/);
  assert.ok(!JSON.stringify(error.error).includes("SECRET"));
  assert.equal(calls.length, 2);
});

test("PUT 连不上（网络 / CORS）：单独一句", async () => {
  const { error } = await runUpload((url) => {
    if (url.endsWith("/assets/upload-url")) return json(201, TICKET);
    throw new TypeError("Failed to fetch");
  });
  assert.equal(error.error.code, "asset.upload.put_failed");
  assert.match(error.error.user_message, /连不上对象存储/);
});

test("complete 失败：保留后端 user_message，标明是登记这一步", async () => {
  const { error } = await runUpload((url) => {
    if (url.endsWith("/assets/upload-url")) return json(201, TICKET);
    if (url === TICKET.upload_url) return new Response(null, { status: 200 });
    return json(409, {
      error: { code: "asset.upload.incomplete", message: "x", user_message: "对象存储里找不到这个文件", retryable: true, trace_id: "t" },
    });
  });
  assert.equal(error.error.code, "asset.upload.complete_failed");
  assert.equal(error.error.user_message, "完成登记失败：对象存储里找不到这个文件");
  assert.equal(error.status, 409);
});

test("创建上传失败：保留后端文案（例如配额满）", async () => {
  const { error, calls } = await runUpload(() =>
    json(413, {
      error: { code: "asset.quota.exceeded", message: "x", user_message: "存储空间不足", retryable: false, trace_id: "t" },
    }),
  );
  assert.equal(error.error.code, "asset.upload.create_failed");
  assert.equal(error.error.user_message, "创建上传失败：存储空间不足");
  assert.equal(calls.length, 1);
});

test("票据不完整（没有 upload_url）：不拿 undefined 去 PUT", async () => {
  const { error, calls } = await runUpload(() => json(201, {}));
  assert.equal(error.error.code, "asset.upload.create_failed");
  assert.equal(calls.length, 1);
});

// ---------------------------------------------------------------- 分镜目录与补图范围（REELBENCH_P1）

const scope = await jiti.import("../lib/freeflow/storyboard-scope.ts");

// 镜号不连续、节点 7 不存在、镜号 5 重复——都是 Agent 实际可能给出的形状
const SB_NODES = [
  { index: 1, summary: "渡口夜雾", scene_ref: "ferry" },
  { index: 2, summary: "茶棚", scene_ref: "tea" },
  { index: 3, summary: "空节点", scene_ref: "none" },
];
const SB_SHOTS = [
  { index: 1, node_index: 1, scene_ref: "ferry", content: "船夫撑篙", dialogue: "" },
  { index: 5, node_index: 1, scene_ref: "ferry", content: "雾里有灯", dialogue: "谁在那边" },
  { index: 9, node_index: 2, scene_ref: "tea", content: "茶客低语", dialogue: "" },
  { index: 12, node_index: 7, scene_ref: "tea", content: "归属不明的一镜", dialogue: "" },
  { index: 5, node_index: 2, scene_ref: "tea", content: "镜号重复", dialogue: "" },
];
const fact = (o = {}) => ({ hasImage: false, status: null, pending: false, outdated: false, ...o });
const SB_FACTS = [
  fact({ hasImage: true, status: "succeeded" }),
  fact({ status: "running" }),
  fact({ status: "failed" }),
  fact(),
  fact({ hasImage: true, status: "succeeded", outdated: true }),
];

test("目录按真实节点分组，未知 node_index 进未归属组，空节点保留", () => {
  const groups = scope.buildDirectory(SB_SHOTS, SB_NODES);
  assert.deepEqual(
    groups.map((g) => [g.nodeIndex, g.positions]),
    [
      [1, [0, 1]],
      [2, [2, 4]],
      [3, []],
      [null, [3]],
    ],
  );
  assert.equal(scope.groupKey(null), "orphan");
  assert.equal(scope.groupKey(2), "n2");
});

test("非连续镜号：JSON Pointer 用数组位置，不用镜号", () => {
  // S05 在数组第 1 位；按镜号算会写到 /shots/5（越界或别的镜）
  const at = SB_SHOTS.findIndex((s) => s.index === 5);
  assert.equal(scope.shotPointer(at, "content"), "/shots/1/content");
  assert.equal(scope.shotPointer(3, "dialogue"), "/shots/3/dialogue");
});

test("筛选后编辑：可见列表只是原数组位置的子集，身份不变", () => {
  const missing = scope.visiblePositions(SB_SHOTS, SB_FACTS, { query: "", filter: "missing" });
  assert.deepEqual(missing, [1, 2, 3]);
  // 在筛选结果里点第 3 行，拿到的是原数组位置 3，指针也按它算
  const picked = missing[2];
  assert.equal(picked, 3);
  assert.equal(SB_SHOTS[picked].content, "归属不明的一镜");
  assert.equal(scope.shotPointer(picked, "content"), "/shots/3/content");
});

test("搜索：镜号、画面、台词、场景名都能找到；未归属镜头也能找到", () => {
  const names = (ref) => ({ ferry: "渡口", tea: "茶棚" })[ref];
  const q = (query) => scope.visiblePositions(SB_SHOTS, SB_FACTS, { query, filter: "all", sceneName: names });
  assert.deepEqual(q("S12"), [3]);
  assert.deepEqual(q("12"), [3]);
  assert.deepEqual(q("s05"), [1, 4]);
  assert.deepEqual(q("谁在那边"), [1]);
  assert.deepEqual(q("茶棚"), [2, 3, 4]);
  assert.deepEqual(q("   "), [0, 1, 2, 3, 4]);
});

test("状态筛选与统计", () => {
  const f = (filter) => scope.visiblePositions(SB_SHOTS, SB_FACTS, { query: "", filter });
  assert.deepEqual(f("failed"), [2]);
  assert.deepEqual(f("running"), [1]);
  assert.deepEqual(f("outdated"), [4]);
  assert.deepEqual(scope.tally([0, 1, 2, 3, 4], SB_FACTS), {
    total: 5, withImage: 2, missing: 3, failed: 1, running: 1, outdated: 1,
  });
});

test("当前节点补图：只提交缺图且不在运行中的，已有图与运行中跳过", () => {
  const groups = scope.buildDirectory(SB_SHOTS, SB_NODES);
  const node1 = groups.find((g) => g.nodeIndex === 1);
  const plan1 = scope.planBatch(node1.positions, SB_FACTS);
  assert.deepEqual(plan1.submit, []);
  assert.equal(plan1.skippedHasImage, 1);
  assert.equal(plan1.skippedInFlight, 1);

  const node2 = groups.find((g) => g.nodeIndex === 2);
  const plan2 = scope.planBatch(node2.positions, SB_FACTS);
  assert.deepEqual(plan2.submit, [2]); // 失败的那镜重新提交，已有图的 S05(重复) 跳过
  assert.equal(plan2.retryingFailed, 1);
  assert.deepEqual(scope.submitIndexes(plan2.submit, SB_SHOTS), [9]);
});

test("本页刚提交、请求还没回来的镜头也不重复提交", () => {
  const facts = [fact({ pending: true }), fact()];
  const plan = scope.planBatch([0, 1], facts);
  assert.deepEqual(plan.submit, [1]);
  assert.equal(plan.skippedInFlight, 1);
});

test("镜号重复的两条只提交一次", () => {
  const shots = [{ index: 5 }, { index: 5 }, { index: 6 }];
  assert.deepEqual(scope.submitIndexes([0, 1, 2], shots), [5, 6]);
});
