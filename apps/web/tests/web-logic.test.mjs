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

// ---------------------------------------------------------------- 剧本目录、写路径与 URL（REELBENCH_P2A）

const sp = await jiti.import("../lib/freeflow/screenplay-scope.ts");

// 集号不连续（2、5）、集号重复（5 出现两次）、场号跨集重复（1-1）、同集场号重复（5-2 两次）、
// 一条旧格式 beat（缺 emotion，且带一个本页不认识的键）、一场缺 hook 键——都是库里真实可能存在的形状
const SP = {
  title: "渡口",
  synopsis: "夜雾里的一桩旧案",
  episodes: [
    {
      index: 2,
      title: "雾起",
      scenes: [
        {
          id: "1-1",
          location: "渡口",
          time_mood: "夜 · 雾",
          character_refs: ["船夫"],
          beats: [
            { kind: "action", character_ref: "", emotion: "", text: "船夫把灯挑高" },
            { kind: "dialogue", character_ref: "船夫", emotion: "警惕", text: "谁在那边" },
          ],
          hook: "灯灭了",
        },
      ],
    },
    {
      index: 5,
      title: "茶棚",
      scenes: [
        {
          id: "1-1",
          location: "茶棚",
          time_mood: "日",
          character_refs: [],
          beats: [{ kind: "dialogue", character_ref: "茶客", text: "听说了吗", legacy_note: "v0" }],
        },
        {
          id: "5-2",
          location: "后院",
          time_mood: "黄昏",
          character_refs: ["茶客"],
          beats: [{ kind: "sfx", character_ref: "", emotion: "", text: "犬吠" }],
          hook: "",
        },
        {
          id: "5-2",
          location: "后院二",
          time_mood: "夜",
          character_refs: [],
          beats: [{ kind: "action", character_ref: "", emotion: "", text: "有人翻墙" }],
          hook: "",
        },
      ],
    },
    { index: 5, title: "重复集号", scenes: [{ id: "9-1", location: "码头", time_mood: "晨", character_refs: [], beats: [{ kind: "vo", character_ref: "旁白", emotion: "", text: "三天后" }], hook: "" }] },
  ],
  node_coverage: [
    { node_index: 1, scene_id: "1-1", merged_into: null },
    { node_index: 2, scene_id: "5-2", merged_into: null },
    { node_index: 3, scene_id: "", merged_into: 2 },
  ],
};

test("剧本目录按数组顺序，展示编号与数组位置分开", () => {
  const groups = sp.buildEpisodes(SP);
  assert.deepEqual(
    groups.map((g) => [g.at, g.index, g.scenes.map((r) => [r.at, r.id])]),
    [
      [0, 2, [[0, "1-1"]]],
      [1, 5, [[0, "1-1"], [1, "5-2"], [2, "5-2"]]],
      [2, 5, [[0, "9-1"]]],
    ],
  );
  assert.equal(sp.episodeLabel(groups[0]), "第 2 集");
  assert.equal(sp.episodeLabel({ at: 3, index: null }), "第 4 项（集号缺失）");
  const counts = sp.tally(SP, groups);
  assert.deepEqual(
    [counts.episodes, counts.scenes, counts.beats, counts.dialogue, counts.covered, counts.coverageTotal],
    [3, 5, 6, 3, 2, 3],
  );
});

test("搜索命中场号、地点、台词与集标题，结果仍是原数组位置", () => {
  const groups = sp.buildEpisodes(SP);
  assert.deepEqual(sp.visibleScenes(SP, groups, "有人翻墙"), ["1:2"]);
  assert.deepEqual(sp.visibleScenes(SP, groups, "5-2"), ["1:1", "1:2"]);
  assert.deepEqual(sp.visibleScenes(SP, groups, "茶棚"), ["1:0", "1:1", "1:2"]);
  assert.deepEqual(sp.visibleScenes(SP, groups, "谁在那边"), ["0:0"]);
  assert.deepEqual(sp.visibleScenes(SP, groups, "不存在的词"), []);
  // 正在编辑的那一场即使不匹配也钉在结果里
  assert.deepEqual(sp.visibleScenes(SP, groups, "谁在那边", { ep: 1, at: 2 }), ["0:0", "1:2"]);
});

test("场号跨集重复时覆盖归属标为不可确定，不猜一集", () => {
  const info = sp.coverageIndex(SP);
  assert.deepEqual(info.byScene["1-1"], [1]);
  assert.deepEqual(new Set(info.duplicatedSceneIds), new Set(["1-1", "5-2"]));
  assert.equal(info.merged, 1);
});

test("筛选后编辑：patch 打在原数组位置，只带改过的字段", () => {
  const groups = sp.buildEpisodes(SP);
  // 搜索「有人翻墙」只剩第二个 5-2，它在原数组里是 episodes[1].scenes[2]
  const [key] = sp.visibleScenes(SP, groups, "有人翻墙");
  const [ep, at] = key.split(":").map(Number);
  const pos = { ep, at };
  const scene = sp.sceneAt(SP, pos);
  const draft = sp.sceneDraftOf(scene);
  draft.location = "后院（改）";
  draft.beats[0].text = "两个人翻墙";
  const plan = sp.planSceneDraft(pos, scene, draft);
  assert.deepEqual(plan.patches, [
    { path: "/episodes/1/scenes/2/location", value: "后院（改）" },
    { path: "/episodes/1/scenes/2/beats/0/text", value: "两个人翻墙" },
  ]);
  assert.deepEqual(plan.blocked, []);
});

test("没改任何字段就没有 patch", () => {
  const pos = { ep: 0, at: 0 };
  const scene = sp.sceneAt(SP, pos);
  const plan = sp.planSceneDraft(pos, scene, sp.sceneDraftOf(scene));
  assert.deepEqual(plan.patches, []);
});

test("缺键的场字段不提交；缺键 beat 整条替换且保全未编辑的扩展键", () => {
  const pos = { ep: 1, at: 0 };
  const scene = sp.sceneAt(SP, pos);
  assert.equal(sp.editableSceneFields(scene).has("hook"), false);
  const draft = sp.sceneDraftOf(scene);
  draft.hook = "有人在听";
  draft.beats[0].emotion = "神秘";
  const plan = sp.planSceneDraft(pos, scene, draft);
  assert.deepEqual(plan.blocked, ["hook"]);
  assert.deepEqual(plan.replacedBeats, [0]);
  assert.deepEqual(plan.patches, [
    {
      path: "/episodes/1/scenes/0/beats/0",
      value: { kind: "dialogue", character_ref: "茶客", text: "听说了吗", legacy_note: "v0", emotion: "神秘" },
    },
  ]);
  // 原对象没被改动
  assert.equal("emotion" in SP.episodes[1].scenes[0].beats[0], false);
});

test("出场角色是整数组替换，顺序有意义", () => {
  const pos = { ep: 0, at: 0 };
  const scene = sp.sceneAt(SP, pos);
  const draft = sp.sceneDraftOf(scene);
  draft.character_refs = ["船夫", "茶客"];
  assert.deepEqual(sp.planSceneDraft(pos, scene, draft).patches, [
    { path: "/episodes/0/scenes/0/character_refs", value: ["船夫", "茶客"] },
  ]);
});

test("URL：query 优先于旧 hash，旧 hash 单独出现时仍可用", () => {
  const groups = sp.buildEpisodes(SP);
  assert.deepEqual(sp.readStoryView("", "#screenplay", groups), { kind: "script" });
  assert.deepEqual(sp.readStoryView("", "#plot-index", groups), { kind: "story" });
  assert.deepEqual(sp.readStoryView("", "", groups), { kind: "story" });
  // 两套同时存在：只看 query
  assert.deepEqual(sp.readStoryView("?view=story", "#screenplay", groups), { kind: "story" });
  assert.deepEqual(sp.readStoryView("?ep=2", "#plot-index", groups), { kind: "episode", at: 0 });
});

test("URL：重复集号、同集重复场号靠数组位置消歧，往返后仍是同一个对象", () => {
  const groups = sp.buildEpisodes(SP);
  const views = [
    { kind: "script" },
    { kind: "episode", at: 0 },
    { kind: "episode", at: 2 }, // 第二个「第 5 集」
    { kind: "scene", ep: 1, at: 2 }, // 同集第二个 5-2
    { kind: "scene", ep: 1, at: 0 }, // 与第 2 集同号的 1-1
  ];
  for (const view of views) {
    const params = sp.storyViewParams(view, groups);
    const search = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null),
    ).toString();
    assert.deepEqual(sp.readStoryView(`?${search}`, "", groups), view, search);
  }
  assert.deepEqual(sp.storyViewParams({ kind: "scene", ep: 1, at: 2 }, groups), {
    view: null, ep: "5", scene: "5-2", epAt: "1", sceneAt: "2",
  });
});

test("URL：只有旧的编号参数时取第一个；位置与编号对不上时信编号；找不到就逐级退化", () => {
  const groups = sp.buildEpisodes(SP);
  assert.deepEqual(sp.readStoryView("?ep=5&scene=5-2", "", groups), { kind: "scene", ep: 1, at: 1 });
  // 返工后顺序变了：位置 0 上现在是第 2 集，不是 URL 说的第 5 集
  assert.deepEqual(sp.readStoryView("?ep=5&epAt=0", "", groups), { kind: "episode", at: 1 });
  assert.deepEqual(sp.readStoryView("?ep=5&scene=7-7", "", groups), { kind: "episode", at: 1 });
  assert.deepEqual(sp.readStoryView("?ep=99&scene=1-1", "", groups), { kind: "script" });
  assert.deepEqual(sp.readStoryView("?epAt=9", "", groups), { kind: "script" });
});

test("改动记录路径按数组位置回查真实集号与场号", () => {
  assert.equal(sp.describeScreenplayPath(SP, "/title"), "剧本标题");
  assert.equal(sp.describeScreenplayPath(SP, "/episodes/2/title"), "第 5 集 · 标题");
  assert.equal(
    sp.describeScreenplayPath(SP, "/episodes/1/scenes/2/beats/0/text"),
    "第 5 集 · 场 5-2 · 第 1 条节拍 · 内容",
  );
  assert.equal(
    sp.describeScreenplayPath(SP, "/episodes/1/scenes/0/beats/0"),
    "第 5 集 · 场 1-1 · 第 1 条节拍（整条）",
  );
  assert.equal(sp.describeScreenplayPath(SP, "/episodes/0/scenes/0/hook"), "第 2 集 · 场 1-1 · 钩子");
});

// ------------------------------------------------------------ P2B 角色 / 场景工作台
const profile = await jiti.import("../lib/freeflow/profile-scope.ts");
const { readFileSync } = await import("node:fs");

test("受控词表与 agents/schemas.py 逐项一致", () => {
  const src = readFileSync(new URL("../../../agents/schemas.py", import.meta.url), "utf8");
  const literal = (name) => {
    const m = src.match(new RegExp(String.raw`^${name} = Literal\[([\s\S]*?)\]`, "m"));
    assert.ok(m, `${name} 不在 schemas.py 里`);
    return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
  };
  for (const name of ["BEING_KINDS", "CAMPS", "HEIGHT_BANDS", "BODY_TYPES", "POSTURES"]) {
    assert.deepEqual([...profile[name]], literal(name), name);
  }
});

const CHARS = {
  characters: [
    { ref: "chuan_fu", name: "船夫", identity: "摆渡人", camp: "中立", kind: "人类", personality: ["沉默"], height: "偏高", hair: "花白", build: "瘦高" },
    { ref: "cha_ke", name: "茶客", identity: "说书人", camp: "未知", personality: ["话多", "机敏"], height: "不存在的词" },
    { ref: "chuan_fu", name: "船夫之子", identity: "少年" },
  ],
};

test("角色：搜索只决定显示，正在编辑的对象钉住；身份是数组位置", () => {
  const rows = profile.buildRows(CHARS, "characters");
  assert.deepEqual(rows.map((r) => r.at), [0, 1, 2]);
  assert.deepEqual(profile.visibleRows(rows, "说书", null), [1]);
  assert.deepEqual(profile.visibleRows(rows, "说书", 2), [1, 2]);
  assert.deepEqual(profile.visibleRows(rows, "CHUAN", null), [0, 2]);
});

test("角色 URL：ref 定位，重复 ref 带位置消歧，找不到退回总览", () => {
  const rows = profile.buildRows(CHARS, "characters");
  assert.deepEqual(profile.profileViewParams({ kind: "item", at: 1 }, rows), { ref: "cha_ke", at: null });
  assert.deepEqual(profile.profileViewParams({ kind: "item", at: 2 }, rows), { ref: "chuan_fu", at: "2" });
  assert.deepEqual(profile.readProfileView("?ref=chuan_fu&at=2", rows), { kind: "item", at: 2 });
  assert.deepEqual(profile.readProfileView("?ref=chuan_fu", rows), { kind: "item", at: 0 });
  assert.deepEqual(profile.readProfileView("?ref=cha_ke&at=2", rows), { kind: "item", at: 1 });
  assert.deepEqual(profile.readProfileView("?ref=nobody", rows), { kind: "overview" });
});

test("角色字段：缺键只读、词表外的值只读、只提交改动且打在原数组位置", () => {
  const f0 = profile.characterFields(CHARS.characters[0], 0);
  const byId = Object.fromEntries(f0.map((f) => [f.id, f]));
  assert.equal(byId.hair.blocked, null);
  assert.equal(byId.eyes.blocked, profile.MISSING_KEY);
  assert.equal(byId.posture.blocked, profile.MISSING_KEY);
  assert.ok(!("build" in byId), "旧 build 不作为可编辑字段");
  const f1 = profile.characterFields(CHARS.characters[1], 1);
  assert.match(Object.fromEntries(f1.map((f) => [f.id, f])).height.blocked, /受控词表/);
  const patches = profile.planPatches(f0, { hair: "全白", name: "船夫", personality: ["沉默", "警惕"], eyes: "x" });
  assert.deepEqual(patches, [
    { path: "/characters/0/personality", value: ["沉默", "警惕"] },
    { path: "/characters/0/hair", value: "全白" },
  ]);
  assert.deepEqual(profile.parseList(" 沉默 \n\n 警惕\n"), ["沉默", "警惕"]);
});

test("场景：旧扁平光照写 /lighting，旧字符串参照物整项替换并保住名称", () => {
  const legacy = { ref: "ferry", name: "渡口", time_slot: "夜", setting: "江边", key_elements: ["船"], camera_axis: { position: "岸上", facing: "江面", far_end: "对岸" }, lighting: "灯笼暖光", fixed_references: ["码头右侧拴着一条破旧的小船"] };
  const fields = profile.sceneFields(legacy, 3);
  const byId = Object.fromEntries(fields.map((f) => [f.id, f]));
  assert.equal(byId["lighting.-1"].label, "默认");
  assert.deepEqual(byId["lighting.-1"].toPatch("冷月"), { path: "/scenes/3/lighting", value: "冷月" });
  assert.equal(byId.default_lighting.blocked, profile.MISSING_KEY);
  assert.deepEqual(byId["fixed.0"].toPatch("码头右侧一条新船"), {
    path: "/scenes/3/fixed_references/0",
    value: { name: "码头右侧拴着一条破旧的小", description: "码头右侧一条新船", origin: "migrated" },
  });
  assert.deepEqual(byId["camera_axis.facing"].toPatch("下游"), { path: "/scenes/3/camera_axis/facing", value: "下游" });
});

test("场景：新形状写子路径，缺名称的半成品整项替换保全其他键，空项/重名整组只读", () => {
  const scene = {
    ref: "ferry", name: "渡口", time_slot: "夜", setting: "江边", key_elements: ["船"],
    camera_axis: { position: "岸上", facing: "江面", far_end: "对岸" },
    lighting_states: [{ name: "晨雾", description: "灰白", origin: "authored" }, { name: "夜灯", description: "暖光" }],
    default_lighting: "夜灯",
    fixed_references: [{ description: "左侧石阶，青苔很厚", extra: 1 }],
  };
  const byId = Object.fromEntries(profile.sceneFields(scene, 0).map((f) => [f.id, f]));
  assert.deepEqual(byId["lighting.1"].toPatch("冷光"), { path: "/scenes/0/lighting_states/1/description", value: "冷光" });
  assert.equal(byId.default_lighting.blocked, null);
  assert.deepEqual([...byId.default_lighting.options], ["晨雾", "夜灯"]);
  assert.deepEqual(byId["fixed.0"].toPatch("右侧石阶"), {
    path: "/scenes/0/fixed_references/0",
    value: { description: "右侧石阶", extra: 1, name: "左侧石阶，青苔很厚", origin: "migrated" },
  });
  const messy = { ...scene, lighting_states: [{ name: "", description: "" }, { name: "晨雾", description: "a" }, { name: "晨雾", description: "b" }] };
  const light = profile.sceneFields(messy, 0).filter((f) => f.group === "光照状态");
  assert.ok(light.length > 0 && light.every((f) => f.blocked !== null), "有空项或重名时整组只读");
  assert.equal(light.find((f) => f.id === "lighting.1").label, "晨雾", "条目按原始下标定位");
});

test("改动记录路径译成对象名与字段名", () => {
  const block = { scenes: [{ ref: "ferry", name: "渡口", lighting_states: [{ name: "晨雾" }, { name: "夜灯" }] }] };
  assert.equal(profile.describeProfilePath(block, "scenes", "/scenes/0/lighting_states/1/description"), "渡口 · 光照状态 夜灯 · 描述");
  assert.equal(profile.describeProfilePath(CHARS, "characters", "/characters/1/hair"), "茶客 · 发型");
});

test("库值变了草稿逐字段跟上：没动过的换新值，动过的保留，刚保存的整份换", () => {
  const prev = { name: "村民三", identity: "住户", personality: ["朴实", "胆小"] };
  const next = { name: "村民3", identity: "住户", personality: ["朴实"] };
  // 撤销：草稿没动过 → 全部换成撤销后的库值（以前会把旧值当成改动留下来）
  assert.deepEqual(profile.rebaseDraft(prev, next, { ...prev }, false), next);
  // 手上改着身份时别处撤销了名称：身份保留草稿，名称跟新值
  assert.deepEqual(profile.rebaseDraft(prev, next, { ...prev, identity: "住户（改）" }, false), { ...next, identity: "住户（改）" });
  // 自己刚保存（后端可能规范化了值）→ 整份换
  assert.deepEqual(profile.rebaseDraft(prev, next, { ...prev, name: "  村民三 " }, true), next);
});

const draftRebase = await jiti.import("../lib/freeflow/draft-rebase.ts");

test("剧本字段 / 场编辑的草稿跟库值：撤销后没动过的换新值，正在输入的不被冲掉", () => {
  const { rebaseDraft } = draftRebase;
  assert.equal(profile.rebaseDraft, rebaseDraft, "角色 / 场景档案用的是同一份实现");
  // 剧本标题与梗概（FieldsEditor，键是写路径）
  const prev = { "/title": "渡口（改）", "/synopsis": "梗概" };
  const next = { "/title": "渡口", "/synopsis": "梗概" };
  assert.deepEqual(rebaseDraft(prev, next, { ...prev }, false), next);
  // 正在改梗概时库里的标题被撤销：标题跟新值，梗概草稿保留
  assert.deepEqual(rebaseDraft(prev, next, { ...prev, "/synopsis": "梗概（打到一半" }, false), { ...next, "/synopsis": "梗概（打到一半" });
  // 换了一集：新路径草稿里没有，直接取新值
  assert.deepEqual(rebaseDraft({ "/episodes/0/title": "雾起" }, { "/episodes/2/title": "码头" }, { "/episodes/0/title": "雾起" }, false), { "/episodes/2/title": "码头" });

  // 场编辑（SceneEditor）：数组字段按内容比较；节拍由调用方逐条再跟一次
  const beat = { kind: "dialogue", character_ref: "chuan_fu", emotion: "", text: "上船" };
  const sPrev = { location: "渡口（改）", time_mood: "夜", character_refs: ["chuan_fu"], hook: "", beats: [beat] };
  const sNext = { ...sPrev, location: "渡口" };
  assert.deepEqual(rebaseDraft(sPrev, sNext, structuredClone(sPrev), false), sNext);
  const typing = { ...structuredClone(sPrev), character_refs: ["chuan_fu", "cha_ke"] };
  assert.deepEqual(rebaseDraft(sPrev, sNext, typing, false).character_refs, ["chuan_fu", "cha_ke"]);
  assert.deepEqual(rebaseDraft(sPrev, sNext, typing, false).location, "渡口");
  const beatNext = { ...beat, emotion: "急" };
  assert.deepEqual(rebaseDraft(beat, beatNext, { ...beat, text: "快上船" }, false), { ...beatNext, text: "快上船" });
});

// ---------------------------------------------------------------- P3A 首页与资产

const recency = await jiti.import("../lib/freeflow/project-recency.ts");
const homeStart = await jiti.import("../lib/freeflow/home-start.ts");
const assetScope = await jiti.import("../lib/freeflow/asset-scope.ts");

test("近期项目按 updated_at 倒序，不拿 created_at 兜底；坏时间排最后；不改入参", () => {
  const rows = [
    { id: "a", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
    { id: "b", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-10-03T08:00:00Z" },
    { id: "c", created_at: "2026-10-02T00:00:00Z", updated_at: "not-a-date" },
    { id: "d", created_at: "2026-09-20T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
  ];
  const before = rows.map((r) => r.id).join();
  assert.deepEqual(recency.sortByRecentEdit(rows).map((r) => r.id), ["b", "a", "d", "c"]);
  assert.equal(rows.map((r) => r.id).join(), before);
});

test("最近编辑时间文案：今天 / 昨天 / 同年 / 跨年 / 坏数据", () => {
  const now = new Date(2026, 9, 3, 18, 0);
  assert.equal(recency.formatEditedAt(new Date(2026, 9, 3, 9, 5).toISOString(), now), "今天 09:05");
  assert.equal(recency.formatEditedAt(new Date(2026, 9, 2, 23, 59).toISOString(), now), "昨天 23:59");
  assert.equal(recency.formatEditedAt(new Date(2026, 8, 28, 14, 5).toISOString(), now), "09-28 14:05");
  assert.equal(recency.formatEditedAt(new Date(2025, 8, 28, 14, 5).toISOString(), now), "2025-09-28");
  assert.equal(recency.formatEditedAt("garbage", now), "时间未知");
});

test("首页两个动作：只创建只要项目名；开始生产要项目名和原文；上限按码点数", () => {
  const base = { title: "", source: "", createdId: null, phase: "idle" };
  let a = homeStart.startAvailability(base);
  assert.equal(a.createOnly.enabled, false);
  assert.equal(a.createOnly.reason, "先填项目名");
  assert.equal(a.start.enabled, false);

  a = homeStart.startAvailability({ ...base, title: "第七夜" });
  assert.equal(a.createOnly.enabled, true);
  assert.equal(a.start.enabled, false);
  assert.match(a.start.reason, /原文/);

  a = homeStart.startAvailability({ ...base, title: "第七夜", source: "雨夜渡口" });
  assert.equal(a.start.enabled, true);

  // 20000 个 emoji 是 40000 个 UTF-16 单元，但按字符算正好在上限内
  const emoji = "😀".repeat(homeStart.SOURCE_MAX);
  assert.equal(homeStart.charCount(emoji), homeStart.SOURCE_MAX);
  assert.equal(homeStart.startAvailability({ ...base, title: "t", source: emoji }).start.enabled, true);
  a = homeStart.startAvailability({ ...base, title: "t", source: emoji + "x" });
  assert.equal(a.start.enabled, false);
  assert.match(a.start.reason, /超过/);

  a = homeStart.startAvailability({ ...base, title: "t", source: "s", phase: "advancing" });
  assert.equal(a.createOnly.enabled, false);
  assert.equal(a.start.enabled, false);
});

test("开始生产失败后重试：只发 advance，不再建项目；项目名不再参与校验", () => {
  assert.deepEqual(homeStart.startPlan({ createdId: null }), { create: true, saveSource: false, advance: true });
  // 重试先 PUT：用户可能改过原文，而 advance 不改写已保存的原文（409 agent.source.conflict）
  assert.deepEqual(homeStart.startPlan({ createdId: "p1" }), { create: false, saveSource: true, advance: true });
  const a = homeStart.startAvailability({ title: "", source: "原文", createdId: "p1", phase: "idle" });
  assert.equal(a.createOnly.enabled, false);
  assert.equal(a.start.enabled, true);
  assert.equal(homeStart.createOnlyNote("  "), null);
  assert.match(homeStart.createOnlyNote("一段原文"), /一起保存进项目/);
});

test("只创建项目：有原文就免费保存；保存失败重试只发 PUT；超长原文先挡住", () => {
  assert.deepEqual(homeStart.createOnlyPlan({ createdId: null, source: "原文" }), { create: true, saveSource: true });
  assert.deepEqual(homeStart.createOnlyPlan({ createdId: null, source: "  " }), { create: true, saveSource: false });
  assert.deepEqual(homeStart.createOnlyPlan({ createdId: "p1", source: "原文" }), { create: false, saveSource: true });
  const base = { title: "t", source: "", createdId: null, phase: "idle" };
  assert.equal(homeStart.startAvailability(base).createOnly.enabled, true);
  const long = homeStart.startAvailability({ ...base, source: "字".repeat(homeStart.SOURCE_MAX + 1) });
  assert.equal(long.createOnly.enabled, false);
  assert.match(long.createOnly.reason, /超过/);
  const saving = homeStart.startAvailability({ ...base, phase: "saving" });
  assert.equal(saving.createOnly.reason, "正在保存原文");
});

test("资产类型筛选与后端 mime.py 一致，text 有入口；项目范围没有 Skill", () => {
  const mime = readFileSync(new URL("../../api/modules/asset/mime.py", import.meta.url), "utf8");
  const backendTypes = new Set([...mime.matchAll(/"[a-z]+\/[a-z0-9.+-]+": "([a-z]+)"/g)].map((m) => m[1]));
  const fileFilters = assetScope.filtersFor("global").filter((f) => assetScope.serverType(f) !== undefined);
  assert.deepEqual([...backendTypes].sort(), [...fileFilters].sort());
  assert.equal(assetScope.serverType("text"), "text");
  assert.equal(assetScope.FILTER_LABEL.text, "文本");
  assert.equal(assetScope.serverType("character"), undefined);
  assert.equal(assetScope.serverType("all"), undefined);
  assert.ok(assetScope.filtersFor("global").includes("skill"));
  assert.ok(!assetScope.filtersFor("project").includes("skill"));
  assert.ok(assetScope.filtersFor("project").includes("text"));
  assert.ok(assetScope.showsFiles("text") && !assetScope.showsFiles("scene"));
  assert.ok(assetScope.showsCharacters("all") && assetScope.showsScenes("scene") && !assetScope.showsScenes("character"));
});

test("资产搜索：多词都要命中、不分大小写；档案按对象名能搜到；来源只认真实项目", () => {
  assert.equal(assetScope.matchesQuery("", "x"), true);
  assert.equal(assetScope.matchesQuery("Ferry 雨", "ferry_night.PNG", "雨夜"), true);
  assert.equal(assetScope.matchesQuery("ferry 晴", "ferry_night.png", "雨夜"), false);
  assert.deepEqual(assetScope.profileNames({ characters: [{ name: "船夫" }, { ref: "x" }, null] }, "characters"), ["船夫"]);
  assert.deepEqual(assetScope.profileNames({ scenes: "bad" }, "scenes"), []);
  const titles = new Map([["p1", "渡口"]]);
  assert.equal(assetScope.sourceLabel(null, titles), "未挂项目");
  assert.equal(assetScope.sourceLabel("p1", titles), "渡口");
  assert.equal(assetScope.sourceLabel("p9", titles), "不在项目列表中");
});

test("预览方式：图/视频/音频直出，文本有大小上限并截断，其余只给下载；翻页去重", () => {
  assert.equal(assetScope.previewKind({ type: "image", size_bytes: 1 }), "image");
  assert.equal(assetScope.previewKind({ type: "text", size_bytes: 10 }), "text");
  assert.equal(assetScope.previewKind({ type: "text", size_bytes: null }), "text");
  assert.equal(assetScope.previewKind({ type: "text", size_bytes: assetScope.TEXT_PREVIEW_MAX_BYTES + 1 }), "none");
  assert.equal(assetScope.previewKind({ type: "document", size_bytes: 1 }), "none");
  assert.equal(assetScope.previewKind({ type: "workflow", size_bytes: 1 }), "none");
  const long = "字".repeat(assetScope.TEXT_PREVIEW_CHARS + 5);
  const clipped = assetScope.clipText(long);
  assert.equal(clipped.clipped, true);
  assert.equal(Array.from(clipped.text).length, assetScope.TEXT_PREVIEW_CHARS);
  assert.deepEqual(assetScope.clipText("短").clipped, false);
  assert.deepEqual(
    assetScope.appendPage([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "c" }]).map((x) => x.id),
    ["a", "b", "c"],
  );
});

test("资产库响应缺列表字段时当空列表、容量为 null；完整响应原样保留", () => {
  const empty = assetScope.normalizeLibrary({ items: [], next_cursor: null });
  assert.deepEqual(empty, {
    usage: null,
    assets: [],
    next_cursor: null,
    profiles: [],
    characters: [],
    folders: [],
  });
  assert.deepEqual(assetScope.normalizeLibrary(null).assets, []);
  const usage = { used_bytes: 1, quota_bytes: null, free_bytes: null, percent_used: 0 };
  const full = { usage, assets: [{ id: "a" }], next_cursor: "c1", profiles: [{ kind: "scenes" }], characters: [], folders: [] };
  assert.deepEqual(assetScope.normalizeLibrary(full), full);
});

// ------------------------------------------------------------ P3B 任务
const taskScope = await jiti.import("../lib/freeflow/task-scope.ts");
const recordScope = await jiti.import("../lib/freeflow/record-scope.ts");

const T = (over = {}) => ({
  id: "t1", project_id: "p1", type: "image.generate", status: "failed", progress: 0, attempt: 1, max_attempts: 3,
  error_code: null, estimated_cost: 120, actual_cost: 0, counts_as_waste: false, output_json: null,
  created_at: "2026-10-01T10:00:00+00:00", started_at: null, finished_at: null, ...over,
});

test("任务行缺字段：数字当 0、时间当 null、未知状态原样保留、没 id 的丢掉", () => {
  const t = taskScope.normalizeTask({ id: "x", status: "paused" });
  assert.equal(t.status, "paused");
  assert.equal(t.type, "unknown");
  assert.equal(t.progress, 0);
  assert.equal(t.estimated_cost, 0);
  assert.equal(t.created_at, null);
  assert.equal(t.error_code, null);
  assert.equal(taskScope.normalizeTask({ status: "queued" }), null);
  assert.equal(taskScope.normalizeTask(null), null);
  assert.equal(taskScope.normalizeTask({ id: "y", progress: 250 }).progress, 100);
  assert.deepEqual(taskScope.normalizeTaskPage({ items: "坏", next_cursor: 3 }), { items: [], next_cursor: null });
  assert.deepEqual(taskScope.normalizeTaskPage(undefined), { items: [], next_cursor: null });
  const page = taskScope.normalizeTaskPage({ items: [T(), null, { nope: 1 }], next_cursor: "2026-10-01" });
  assert.equal(page.items.length, 1);
  assert.equal(page.next_cursor, "2026-10-01");
  assert.equal(taskScope.isKnownStatus("paused"), false);
  assert.equal(taskScope.isKnownStatus("cancelled"), true);
});

test("筛选分栏：失败与取消分开，进行中 = 排队 + 生成中", () => {
  const b = (s) => taskScope.TASK_BUCKETS.filter((x) => taskScope.inBucket(s, x.key)).map((x) => x.key);
  assert.deepEqual(b("queued"), ["all", "active"]);
  assert.deepEqual(b("running"), ["all", "active"]);
  assert.deepEqual(b("succeeded"), ["all", "succeeded"]);
  assert.deepEqual(b("failed"), ["all", "failed"]);
  assert.deepEqual(b("cancelled"), ["all", "cancelled"]);
  assert.deepEqual(b("paused"), ["all"]);
});

test("取消只给 queued / running；重试只给 failed 且错误码可重试（空码、未登记的码放行）", () => {
  for (const s of ["queued", "running"]) assert.equal(taskScope.canCancel({ status: s }), true, s);
  for (const s of ["succeeded", "failed", "cancelled"]) assert.equal(taskScope.canCancel({ status: s }), false, s);
  assert.equal(taskScope.canRetry({ status: "failed", error_code: null }), true);
  assert.equal(taskScope.canRetry({ status: "failed", error_code: "provider.transient.timeout" }), true);
  assert.equal(taskScope.canRetry({ status: "failed", error_code: "x.never.registered" }), true);
  assert.equal(taskScope.canRetry({ status: "failed", error_code: "billing.credit.insufficient" }), false);
  assert.equal(taskScope.canRetry({ status: "failed", error_code: "prompt.run.stale" }), false);
  for (const s of ["queued", "running", "succeeded", "cancelled"]) {
    assert.equal(taskScope.canRetry({ status: s, error_code: null }), false, s);
  }
});

test("不可重试码表与 core/errors.py 逐项一致（后端加码不同步这里就红）", () => {
  const src = readFileSync(new URL("../../api/core/errors.py", import.meta.url), "utf8");
  const body = src.slice(src.indexOf("ERRORS: dict[str, ErrorSpec] = {"), src.indexOf("\nclass AppError"));
  const keys = [...body.matchAll(/^ {4}"([a-z_]+(?:\.[a-z_]+)+)": ErrorSpec\(/gm)];
  assert.ok(keys.length > 30, `只解析到 ${keys.length} 个错误码`);
  const nonRetryable = [];
  keys.forEach((m, i) => {
    const block = body.slice(m.index, i + 1 < keys.length ? keys[i + 1].index : body.length);
    if (!block.includes("retryable=True")) nonRetryable.push(m[1]);
  });
  assert.deepEqual([...taskScope.NON_RETRYABLE_ERRORS].sort(), nonRetryable.sort());
});

test("重试说明写出预扣数字、沿用当时输入；取消说明写明不能再重试", () => {
  const lines = taskScope.retryNotice({ estimated_cost: 1200 });
  assert.match(lines[0], /重新预扣 1,200 Credits/);
  assert.ok(lines.some((l) => l.includes("沿用这条任务当时的输入")));
  assert.match(taskScope.retryNotice({ estimated_cost: 0 })[0], /预估费用/);
  assert.ok(taskScope.CANCEL_NOTICE.some((l) => l.includes("不能再重试")));
});

test("失败原因：空码不编原因，未登记的码说未归类", () => {
  assert.equal(taskScope.failReason(null), "没有记录失败原因");
  assert.equal(taskScope.failReason("x.y.z"), "未归类的失败");
  assert.match(taskScope.failReason("billing.credit.insufficient"), /余额不足/);
});

test("对象定位：角色/场景走 ?ref=，镜头走 ?shot=，缺 ref 落到列表页", () => {
  const base = "/freeflow/projects/p1";
  assert.deepEqual(taskScope.subjectOf({ subject_kind: "character", subject_ref: "chuan fu", shot_index: null }, "p1"), {
    label: "角色 chuan fu", href: `${base}/characters?ref=chuan%20fu`,
  });
  assert.deepEqual(taskScope.subjectOf({ subject_kind: "scene", subject_ref: "ferry", shot_index: null }, "p1"), {
    label: "场景 ferry", href: `${base}/scenes?ref=ferry`,
  });
  assert.deepEqual(taskScope.subjectOf({ subject_kind: "shot", subject_ref: null, shot_index: 3 }, "p1"), {
    label: "镜头 3", href: `${base}/storyboard?shot=3`,
  });
  assert.deepEqual(taskScope.subjectOf({ subject_kind: "scene", subject_ref: null, shot_index: null }, "p1"), {
    label: "场景", href: `${base}/scenes`,
  });
  const map = taskScope.subjectsByTask(
    [{ task_id: "t1", subject_kind: "shot", subject_ref: null, shot_index: 2 }, { task_id: null, subject_kind: "character", subject_ref: "a" }, null],
    "p1",
  );
  assert.deepEqual([...map.keys()], ["t1"]);
  assert.equal(taskScope.subjectsByTask({ items: [] }, "p1").size, 0);
});

test("SSE 快照只覆盖状态几列；刷新首页保留已加载的更早页", () => {
  const rows = [T({ id: "a", status: "queued", estimated_cost: 50 }), T({ id: "b" })];
  const merged = taskScope.mergeLive(rows, new Map([["a", { task_id: "a", status: "running", progress: 40, attempt: 1, error_code: null, actual_cost: 0 }]]));
  assert.equal(merged[0].status, "running");
  assert.equal(merged[0].progress, 40);
  assert.equal(merged[0].estimated_cost, 50);
  assert.equal(merged[1], rows[1]);

  const at = (id, h) => T({ id, created_at: `2026-10-01T${h}:00:00+00:00` });
  const loaded = [at("c", "12"), at("b", "11"), at("a", "10"), at("z", "09")];
  const first = [at("d", "13"), at("c", "12"), at("b", "11")];
  assert.deepEqual(taskScope.mergeFirstPage(first, loaded).map((t) => t.id), ["d", "c", "b", "a", "z"]);
  assert.deepEqual(taskScope.mergeFirstPage([], loaded), []);
  assert.deepEqual(taskScope.appendPage([at("a", "10")], [at("a", "10"), at("z", "09")]).map((t) => t.id), ["a", "z"]);
});

test("时间与成本的空值可读", () => {
  assert.equal(taskScope.timeText(null), "时间未记录");
  assert.equal(taskScope.timeText("不是时间"), "时间未记录");
  assert.notEqual(taskScope.timeText("2026-10-01T10:00:00Z"), "时间未记录");
  assert.equal(taskScope.costText({ actual_cost: 0, estimated_cost: 0 }), null);
  assert.equal(taskScope.costText({ actual_cost: 0, estimated_cost: 120 }), "预估 120 Credits");
  assert.equal(taskScope.costText({ actual_cost: 98, estimated_cost: 120 }), "实扣 98 Credits");
});

test("生成记录缺字段：标题、时间、产物、步骤都有可读的空值", () => {
  const rows = recordScope.normalizeRecords([
    { id: "r1", record_type: "agent" },
    { id: "r2", record_type: "image", asset_ids: "坏", created_at: "" },
    { id: "r3", record_type: "video" },
    null,
  ]);
  assert.deepEqual(rows.map((r) => [r.id, r.title, r.status, r.created_at, r.asset_ids]), [
    ["r1", "文本步骤", "unknown", null, []],
    ["r2", "出图", "unknown", null, []],
  ]);
  assert.deepEqual(recordScope.normalizeRecords({ items: [] }), []);
  const d = recordScope.normalizeDetail({ id: "r1", record_type: "agent", steps: [null, { kind: "validate" }], output: "x" });
  assert.equal(d.prompt, null);
  assert.equal(d.incomplete, false);
  assert.deepEqual(d.steps.map((s) => [s.index, s.kind, s.duration_ms, s.error]), [[0, "validate", 0, null]]);
  assert.equal(d.output, null);
  assert.equal(recordScope.normalizeDetail({ record_type: "agent" }), null);
});

// ---------------------------------------------------------------- 模型与供应商（ADR-039 A2）

const prov = await jiti.import("../lib/freeflow/provider-scope.ts");
const CID = "11111111-2222-3333-4444-555555555555";

test("连接引用串：带模型 / 不带模型 / 模型里有冒号 / 不是连接", () => {
  assert.equal(prov.orgRef(CID, "gpt-4o"), `provider.org:${CID}:gpt-4o`);
  assert.equal(prov.orgRef(CID), `provider.org:${CID}`);
  assert.deepEqual(prov.parseOrgRef(`provider.org:${CID}:ns:model-a`), { connectionId: CID, modelId: "ns:model-a" });
  assert.deepEqual(prov.parseOrgRef(`provider.org:${CID}`), { connectionId: CID, modelId: null });
  assert.equal(prov.parseOrgRef("deepseek-chat"), null);
  assert.equal(prov.parseOrgRef("provider.org:"), null);
  assert.equal(prov.parseOrgRef(undefined), null);
});

test("测试连接三种结论：无法免费验证与限流不算失败", () => {
  assert.equal(prov.classifyTest({ ok: true, message: "鉴权通过", error_code: null }), "pass");
  assert.equal(
    prov.classifyTest({ ok: true, message: "地址可达；该上游不提供模型列表，Key 是否可用要以第一次出图为准", error_code: null }),
    "neutral",
  );
  assert.equal(prov.classifyTest({ ok: false, message: "上游限流，暂时无法验证", error_code: "provider.rate_limit.exceeded" }), "neutral");
  assert.equal(prov.classifyTest({ ok: false, message: "Key 无效", error_code: "provider.byok.rejected" }), "fail");
  assert.equal(prov.VERDICT_LABEL.neutral, "无法免费验证");
});

test("坏默认与 byok 拒绝的原因都翻成人话，未知码不吞", () => {
  assert.equal(prov.brokenReasonText(null), null);
  assert.equal(prov.brokenReasonText("connection_disabled"), "指向的供应商已停用");
  assert.match(prov.brokenReasonText("weird_code"), /weird_code/);
  const e = (reason) => ({ code: "provider.byok.rejected", message: "m", user_message: "你自己配置的模型供应商调用失败", detail: { reason } });
  assert.match(prov.describeApiError(e("reasoning_model_not_allowed")), /推理模型/);
  assert.match(prov.describeApiError(e("connection_missing")), /已被删除/);
  assert.equal(prov.describeApiError(e("unknown")), "你自己配置的模型供应商调用失败");
  assert.equal(prov.describeApiError({ code: "common.validation_failed", message: "Base URL 必须是 https", user_message: "请求参数有误" }, true), "Base URL 必须是 https");
  assert.equal(prov.describeApiError({ code: "common.validation_failed", message: "x", user_message: "请求参数有误" }), "请求参数有误");
});

test("自带上游 404 不说成 Key 错：说清接口、模型与下一步；只有 401/403 才说 Key 被拒", () => {
  const e = (detail) => ({ code: "provider.byok.rejected", message: "m", user_message: "你自己配置的模型供应商调用失败", detail });
  const notFound = prov.describeApiError(
    e({
      reason: "upstream_not_found",
      capability: "text_generation",
      http_status: 404,
      operation: "POST /v1/chat/completions",
      upstream_error_code: "not_found",
      model_id: "gpt-6.1-sol",
    }),
  );
  assert.match(notFound, /POST \/v1\/chat\/completions 返回 HTTP 404 not_found/);
  assert.match(notFound, /模型 gpt-6\.1-sol 不能通过它调用/);
  assert.match(notFound, /不是 API Key 的问题/);
  assert.match(notFound, /只读取模型列表/);
  assert.match(notFound, /OpenAI Chat Completions/);
  assert.doesNotMatch(notFound, /更换 Key/);
  const auth = prov.describeApiError(e({ reason: "upstream_auth_rejected", http_status: 401, model_id: "m1" }));
  assert.match(auth, /拒绝了你的 API Key（HTTP 401）/);
  assert.match(auth, /更换 Key/);
  assert.match(prov.describeApiError(e({ reason: "upstream_model_not_found", http_status: 404, upstream_error_code: "model_not_found", model_id: "m1" })), /核对模型 ID/);
  assert.match(prov.describeApiError(e({ reason: "upstream_redirect", http_status: 302 })), /最终地址/);
  // 没有模型 ID 时不出现 "模型 undefined"
  assert.doesNotMatch(prov.describeApiError(e({ reason: "upstream_not_found", http_status: 404 })), /undefined|null/);
  assert.equal(prov.upstreamFailureText({ reason: "something_new" }), null);
});

test("测试连接只读了模型列表：结论画成中性，不画成「连接正常」", () => {
  const msg =
    "模型列表接口鉴权通过，列表（24 个）里有 gpt-6.1-sol。只读取了模型列表（GET /models，不发生成请求），没有试调用生成接口 POST /chat/completions；该模型能否用这个接口生成，以第一次生成为准";
  assert.equal(prov.classifyTest({ ok: true, message: msg, error_code: null }), "neutral");
  assert.equal(modelOptions.normalizeApiAddress("https://relay.example/v1/responses"), "https://relay.example/v1");
});

test("没有可用模型：按码判断、能力名翻成中文、给模型库地址，且不可重试", () => {
  assert.equal(prov.NOT_CONFIGURED, "provider.not_configured");
  assert.equal(prov.MODELS_HREF, "/freeflow/models");
  assert.equal(prov.needsModelSetup("provider.not_configured"), true);
  for (const c of ["provider.unavailable", "provider.byok.rejected", null, undefined, ""]) {
    assert.equal(prov.needsModelSetup(c), false, String(c));
  }
  const e = (capability) => ({
    code: "provider.not_configured",
    message: "没有可用的模型来完成「text_generation」",
    user_message: "还没有可用的模型：请到「模型库」添加供应商并设为默认，或联系管理员配置平台 Key",
    detail: capability === undefined ? undefined : { capability },
  });
  assert.equal(
    prov.describeApiError(e("text_generation")),
    "还没有可用的文本生成模型：请到「模型库」添加供应商并设为默认，或联系管理员配置平台 Key",
  );
  assert.match(prov.describeApiError(e("image_generation")), /^还没有可用的图片生成模型/);
  // 认不出的能力、没有 detail：用后端原句，不把 `video_generation` 这种内部名直出给用户
  assert.equal(prov.describeApiError(e("video_generation")), e().user_message);
  assert.equal(prov.describeApiError(e()), e().user_message);
  for (const text of [prov.describeApiError(e("text_generation")), prov.describeApiError(e())]) {
    assert.doesNotMatch(text, /text_generation|备用通道/);
  }
  // 上游故障仍是原码原文案，不被新码吞掉
  assert.equal(prov.describeApiError({ code: "provider.unavailable", user_message: "正在切换备用通道" }), "正在切换备用通道");
  assert.equal(taskScope.canRetry({ status: "failed", error_code: "provider.not_configured" }), false);
  assert.equal(taskScope.canRetry({ status: "failed", error_code: "provider.unavailable" }), true);
  assert.match(taskScope.failReason("provider.not_configured"), /模型库/);
});

const CFG = {
  capability: "text_generation", label: "文本生成", available: true, configurable: true, credentials: [], unavailable_reason: null,
  supports_org_connections: true,
  selection: { provider_id: `provider.org:${CID}`, model_id: null, key_source: "org", layer: "org", updated_at: null, broken_reason: null },
  providers: [
    { provider_id: "provider.deepseek", label: "DeepSeek", kind: "catalog", available: true, models: [{ model_id: "deepseek-chat", label: "快", note: "" }], default_model_id: "deepseek-chat", supports_platform_key: true, unavailable_reason: null },
    { provider_id: `provider.org:${CID}`, label: "公司网关", kind: "org", connection_id: CID, available: true, consistency_verified: null,
      models: [{ model_id: "m-a", label: "m-a", note: "" }, { model_id: "m-r", label: "m-r", note: "" }], default_model_id: null, supports_platform_key: false, unavailable_reason: null },
    { provider_id: "provider.org:dead", label: "停用的", kind: "org", connection_id: "dead", available: false, consistency_verified: false,
      models: [{ model_id: "img", label: "img", note: "" }], default_model_id: null, supports_platform_key: false, unavailable_reason: "该供应商已停用" },
  ],
};
const CONNS = [{ id: CID, provider_id: `provider.org:${CID}`, label: "公司网关", preset_id: null, base_url: "https://gw.example.com/v1", enabled: true, masked_key: "sk-••••abcd", created_at: "", updated_at: "",
  models: [{ model_id: "m-a", protocol: "openai_chat", capability: "text_generation", consistency_verified: null }, { model_id: "m-r", protocol: "openai_chat", capability: "text_generation", consistency_verified: null, reasoning: true }] }];

test("默认候选：目录一家一行、连接每个模型一行；model_id 为空时第一个模型是当前；推理与未实测标记", () => {
  const rows = prov.defaultRows(CFG, CONNS);
  assert.deepEqual(rows.map((r) => [r.kind, r.kind === "org" ? r.modelId : r.providerId, r.current]), [
    ["catalog", "provider.deepseek", false],
    ["org", "m-a", true],
    ["org", "m-r", false],
    ["org", "img", false],
  ]);
  assert.equal(rows[2].reasoning, true);
  assert.equal(rows[1].reasoning, false);
  assert.equal(rows[3].unverified, true);
  assert.equal(rows[3].available, false);
  assert.equal(prov.currentDefaultText(CFG), "供应商 公司网关 · m-a");
  const platform = { ...CFG, selection: { ...CFG.selection, provider_id: "provider.deepseek", model_id: null, key_source: "platform", layer: "platform" } };
  assert.match(prov.currentDefaultText(platform), /^未设置，使用平台默认（DeepSeek）/);
  assert.equal(prov.defaultRows(platform, CONNS).some((r) => r.current), false);
});

test("草稿：预设带出地址与模型、编辑时 Key 从空开始", () => {
  const preset = { preset_id: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com/v1", docs_url: "d", key_url: null, icon: "", protocols: ["openai_chat"], capabilities: ["text_generation"],
    models: [{ model_id: "deepseek-chat", protocol: "openai_chat", capability: "text_generation", consistency_verified: null }] };
  const d = prov.draftFromPreset(preset, []);
  assert.deepEqual(d, { presetId: "deepseek", label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", apiKey: "", models: [{ model_id: "deepseek-chat", protocol: "openai_chat", reasoning: false }] });
  const custom = prov.draftFromPreset(null, [{ protocol: "openai_chat", capability: "text_generation", label: "OpenAI 兼容", consistency_verified: null }]);
  assert.equal(custom.presetId, null);
  assert.equal(custom.models[0].protocol, "openai_chat");
  const e = prov.draftFromConnection(CONNS[0]);
  assert.equal(e.apiKey, "");
  assert.deepEqual(e.models.map((m) => m.reasoning), [false, true]);
});

test("前端轻校验：空项、非 https、Key 长度、模型空白与重复", () => {
  const ok = { presetId: null, label: "网关", baseUrl: "https://x.example.com/v1", apiKey: "sk-12345678", models: [{ model_id: "m", protocol: "openai_chat", reasoning: false }] };
  assert.deepEqual(prov.draftProblems(ok, "create"), []);
  assert.deepEqual(prov.draftProblems({ ...ok, apiKey: "" }, "edit"), []);
  assert.ok(prov.draftProblems({ ...ok, apiKey: "" }, "create").includes("填写 API Key"));
  assert.ok(prov.draftProblems({ ...ok, apiKey: "short" }, "edit").some((p) => p.includes("8–512")));
  assert.ok(prov.draftProblems({ ...ok, baseUrl: "http://x.example.com" }, "create").some((p) => p.includes("https")));
  assert.ok(prov.draftProblems({ ...ok, models: [{ model_id: "a b", protocol: "openai_chat", reasoning: false }] }, "create").includes("模型 ID 不能含空白"));
  assert.ok(prov.draftProblems({ ...ok, models: [ok.models[0], ok.models[0]] }, "create").includes("模型重复"));
  assert.ok(prov.draftProblems({ ...ok, models: [{ model_id: " ", protocol: "openai_chat", reasoning: false }] }, "create").includes("至少填一个模型"));
});

test("请求体：新建带 reasoning；PATCH 只发改动、Key 为空不带键", () => {
  const draft = { ...prov.draftFromConnection(CONNS[0]) };
  assert.deepEqual(prov.patchBody(draft, CONNS[0]), {});
  const withKey = { ...draft, apiKey: "  sk-new-key-123  " };
  assert.deepEqual(prov.patchBody(withKey, CONNS[0]), { api_key: "sk-new-key-123" });
  const flip = { ...draft, models: draft.models.map((m, i) => (i === 0 ? { ...m, reasoning: true } : m)) };
  assert.deepEqual(prov.patchBody(flip, CONNS[0]).models.map((m) => m.reasoning), [true, true]);
  assert.equal("api_key" in prov.patchBody(flip, CONNS[0]), false);
  const body = prov.createBody({ presetId: "zhipu", label: " 智谱 ", baseUrl: " https://z.example.com/v4 ", apiKey: " k-12345678 ", models: [{ model_id: " glm ", protocol: "openai_chat", reasoning: true }, { model_id: "", protocol: "openai_chat", reasoning: false }] });
  assert.deepEqual(body, { label: "智谱", base_url: "https://z.example.com/v4", models: [{ model_id: "glm", protocol: "openai_chat", reasoning: true }], api_key: "k-12345678", preset_id: "zhipu" });
});

test("测试连接参数：草稿缺 Key 不能测；已保存的只带改过的地址与 Key", () => {
  const draft = { presetId: null, label: "x", baseUrl: "https://x.example.com/v1", apiKey: "", models: [{ model_id: "m", protocol: "openai_chat", reasoning: false }] };
  assert.equal(prov.draftTestBody(draft), null);
  assert.deepEqual(prov.draftTestBody({ ...draft, apiKey: "sk-12345678" }), { protocol: "openai_chat", model_id: "m", base_url: "https://x.example.com/v1", api_key: "sk-12345678" });
  const saved = prov.draftFromConnection(CONNS[0]);
  assert.deepEqual(prov.savedTestBody(saved, CONNS[0]), { protocol: "openai_chat", model_id: "m-a" });
  assert.deepEqual(prov.savedTestBody({ ...saved, baseUrl: "https://new.example.com/v1" }, CONNS[0]), { protocol: "openai_chat", model_id: "m-a", base_url: "https://new.example.com/v1" });
});

test("删除确认的引用清单与来源文案", () => {
  const lines = prov.referenceLines(
    { defaults: [{ capability: "text_generation" }], projects: [{ project_id: "p", name: "第七夜", capability: "image_generation" }] },
    (c) => ({ text_generation: "文本", image_generation: "出图" })[c],
  );
  assert.deepEqual(lines, ["组织默认 · 文本", "项目「第七夜」· 出图"]);
  assert.equal(prov.sourceText({ preset_id: null }, []), "自定义");
  assert.equal(prov.sourceText({ preset_id: "zhipu" }, [{ preset_id: "zhipu", label: "智谱" }]), "预设 · 智谱");
  assert.equal(prov.sourceText({ preset_id: "gone" }, []), "预设 · gone");
});

test("api.ts 不再引用已删除的自定义端点路由", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../lib/api.ts", import.meta.url), "utf8");
  assert.equal(/custom-endpoint|custom_endpoint|CUSTOM_TEXT_PROVIDER_ID/.test(src), false);
  assert.equal(typeof api.modelConfig.references, "function");
});


const modelOptions = await jiti.import("../lib/freeflow/model-options.ts");
test("读取候选保留手填及已选模型，完整地址与执行 base 一致", () => {
  assert.deepEqual(modelOptions.mergeModelOptions(["remote-a", "remote-a"], ["custom-b", "remote-a"]), ["remote-a", "custom-b"]);
  assert.equal(modelOptions.normalizeApiAddress("https://gateway.example/v4/chat/completions/"), "https://gateway.example/v4");
  assert.equal(modelOptions.normalizeApiAddress("https://gateway.example"), "https://gateway.example/v1");
  assert.equal(modelOptions.normalizeApiAddress("https://gateway.example/chat/completions"), "https://gateway.example/v1");
  assert.equal(modelOptions.normalizeApiAddress("https://gateway.example/v4/images/generations"), "https://gateway.example/v4");
  const draft = { presetId: null, label: "", baseUrl: "https://gateway.example/v1/chat/completions", apiKey: "secret-test-key", models: [{ model_id: "arbitrary-custom", protocol: "openai_chat", reasoning: false }] };
  assert.deepEqual(prov.draftProblems(draft, "create"), []);
  assert.equal(prov.createBody(draft).label, "gateway.example");
  assert.equal(prov.createBody(draft).base_url, "https://gateway.example/v1");
  assert.equal(prov.draftTestBody(draft).base_url, prov.createBody(draft).base_url);
});

test("平台行计费来源：没存官方 Key 时禁用并写明它和供应商连接不是一回事", () => {
  const p = { label: "DeepSeek", supports_platform_key: true };
  const none = prov.billingChoices({ configurable: true }, p, false);
  assert.equal(none.platform.disabled, false);
  assert.equal(none.own.disabled, true);
  assert.equal(none.own.label, "我的 DeepSeek 官方 Key");
  assert.match(none.own.reason, /配置 DeepSeek Key/);
  assert.match(none.own.reason, /我的供应商/);
  const saved = prov.billingChoices({ configurable: true }, p, true);
  assert.equal(saved.own.disabled, false);
  assert.equal(saved.own.reason, null);
  // 平台不给额度：平台项禁用并说明
  const byok = prov.billingChoices({ configurable: true }, { label: "X", supports_platform_key: false }, true);
  assert.equal(byok.platform.disabled, true);
  assert.match(byok.platform.reason, /没有提供 X 的额度/);
  // Skill 锁住：两项都禁用，即使存了 Key 也不放开
  const locked = prov.billingChoices({ configurable: false }, p, true);
  assert.equal(locked.platform.disabled && locked.own.disabled, true);
});

test("当前默认文案：自有计费写明是哪家官方 Key", () => {
  const cfg = {
    providers: [{ provider_id: "provider.deepseek", kind: "catalog", label: "DeepSeek", models: [] }],
    selection: { layer: "org", provider_id: "provider.deepseek", model_id: null, key_source: "org" },
  };
  assert.equal(prov.currentDefaultText(cfg), "平台 DeepSeek · 目录默认顺序 · 我的 DeepSeek 官方 Key");
});

const storySource = await jiti.import("../lib/freeflow/story-source.ts");

test("导入原文：UTF-8（含 BOM）、GBK、UTF-16 都能读；二进制、空文件、非 UTF-8/GBK 给出可修复的原因", () => {
  const utf8 = new TextEncoder().encode("雨夜，少女在旧车站。\r\n第二行");
  let r = storySource.decodeStoryFile(utf8);
  assert.equal(r.ok, true);
  assert.equal(r.encoding, "utf-8");
  assert.equal(r.text, "雨夜，少女在旧车站。\n第二行");

  const bom = new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode("带BOM")]);
  assert.equal(storySource.decodeStoryFile(bom).text, "带BOM");

  // "雨夜" 的 GBK 编码
  const gbk = new Uint8Array([0xd3, 0xea, 0xd2, 0xb9]);
  r = storySource.decodeStoryFile(gbk);
  assert.equal(r.ok, true);
  assert.equal(r.encoding, "gb18030");
  assert.equal(r.text, "雨夜");

  const utf16 = new Uint8Array([0xff, 0xfe, 0x68, 0x00, 0x69, 0x00]);
  assert.equal(storySource.decodeStoryFile(utf16).text, "hi");

  // .docx 是 zip：PK 头后面跟着 NUL
  const zip = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0x06, 0x00]);
  r = storySource.decodeStoryFile(zip);
  assert.equal(r.ok, false);
  assert.match(r.error, /不是纯文本/);

  assert.match(storySource.decodeStoryFile(new Uint8Array()).error, /空的/);
  assert.match(storySource.decodeStoryFile(new TextEncoder().encode(" \n\t ")).error, /没有文字/);
});

test("原文校验：空白不行，上限按码点数，超了写出实际字数", () => {
  assert.match(storySource.sourceProblem("  \n"), /空/);
  assert.equal(storySource.sourceProblem("一句创意"), null);
  assert.equal(storySource.sourceProblem("😀".repeat(storySource.SOURCE_MAX)), null);
  assert.match(storySource.sourceProblem("字".repeat(storySource.SOURCE_MAX + 5)), /20,005 字/);
  assert.equal(storySource.sourceDirty(" 原文 ", "原文"), false);
  assert.equal(storySource.sourceDirty("原文2", "原文"), true);
});

test("拒收原因：类型、大小、数量翻成人话，未知码不吞", () => {
  assert.match(storySource.fileRejectionText("file-invalid-type", ""), /\.docx/);
  assert.match(storySource.fileRejectionText("file-too-large", ""), /1 MB/);
  assert.match(storySource.fileRejectionText("too-many-files", ""), /一个/);
  assert.equal(storySource.fileRejectionText("weird", "原文"), "原文");
});

test("本地草稿：按账号与项目隔离；坏数据与存储异常都当没有；与已保存一致不恢复", () => {
  const mem = new Map();
  const store = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) };
  const a = storySource.draftKey("u1", "p1");
  assert.notEqual(a, storySource.draftKey("u2", "p1"));
  assert.notEqual(a, storySource.draftKey("u1", "p2"));

  storySource.writeDraft(store, a, "草稿", new Date("2026-10-08T00:00:00Z"));
  assert.deepEqual(storySource.readDraft(store, a), { text: "草稿", savedAt: "2026-10-08T00:00:00.000Z" });
  assert.equal(storySource.readDraft(store, storySource.draftKey("u2", "p1")), null);

  assert.equal(storySource.draftToRestore(storySource.readDraft(store, a), "草稿"), null);
  assert.equal(storySource.draftToRestore(storySource.readDraft(store, a), "旧的").text, "草稿");

  storySource.writeDraft(store, a, "   ");
  assert.equal(mem.has(a), false, "空草稿不留");

  mem.set(a, "{not json");
  assert.equal(storySource.readDraft(store, a), null);
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("quota"); }, removeItem: () => { throw new Error("x"); } };
  assert.equal(storySource.readDraft(broken, a), null);
  storySource.writeDraft(broken, a, "x");
  storySource.clearDraft(broken, a);
  assert.equal(storySource.readDraft(null, a), null);
});

test("保存原文：PUT 返回值直接并进快照，不靠重拉", () => {
  const now = new Date("2026-10-08T12:00:00Z");
  const prev = {
    project_id: "p1",
    stage: "plot_index",
    current_state_json: { source: "旧原文", stage: "plot_index", router: { route: "X" }, extra: 1 },
    stale_roles: [],
    updated_at: "2026-10-01T00:00:00Z",
  };
  // 原文变了、后端退回 routing：router 作废，阶段跟着退回
  const changed = storySource.applySavedSource(
    prev,
    { source: "新原文", chars: 3, stage: "routing", changed: true },
    "p1",
    now,
  );
  assert.equal(changed.current_state_json.source, "新原文");
  assert.equal(changed.stage, "routing");
  assert.equal(changed.current_state_json.stage, "routing");
  assert.equal("router" in changed.current_state_json, false);
  assert.equal(changed.current_state_json.extra, 1, "其余字段原样保留");
  assert.equal(changed.updated_at, now.toISOString());
  assert.equal(prev.current_state_json.source, "旧原文", "不原地改旧快照");

  // 同一份原文：什么都不该动
  const same = storySource.applySavedSource(
    prev,
    { source: "旧原文", chars: 3, stage: "plot_index", changed: false },
    "p1",
    now,
  );
  assert.equal(same.stage, "plot_index");
  assert.deepEqual(same.current_state_json.router, { route: "X" });
  assert.equal(same.updated_at, prev.updated_at);

  // 快照没取到（state 接口失败过）：造最小快照，原文仍然可见
  const fresh = storySource.applySavedSource(
    null,
    { source: "原文", chars: 2, stage: "routing", changed: true },
    "p9",
    now,
  );
  assert.equal(fresh.project_id, "p9");
  assert.equal(fresh.current_state_json.source, "原文");
  assert.deepEqual(fresh.stale_roles, []);
  // 并回之后面板不再显示"有未保存的修改"
  assert.equal(storySource.sourceDirty("  原文\n", fresh.current_state_json.source), false);
});
