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
