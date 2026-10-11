#!/usr/bin/env python3
"""Build a fast GitHub Pages frontend from the verified standalone AMBOSS HTML.

The landing document keeps metadata and a compact QID index. Each test's complete
question payload is emitted as a separate JSON asset and fetched only when that
test (or a custom practice selection) is opened. The original, unchanged HTML is
also published as an optional offline download.
"""
from __future__ import annotations

import gzip
import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "AMBOSSQBank.html.gz"
ROUTES = ROOT / "manus-routes.json"
OUT = ROOT / "_site"
EXPECTED_TESTS = 1266
EXPECTED_QUESTIONS = 21247
MAX_SITE_BYTES = 1_000_000_000
ARRAY_MARKER = "const TESTS_LIST = "


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one {label} occurrence; found {count}")
    return source.replace(old, new, 1)


def plain_preview(value: object, limit: int = 120) -> str:
    text = str(value or "")
    text = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1\s*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def main() -> None:
    if not SOURCE.is_file() or not ROUTES.is_file():
        raise FileNotFoundError("Expected AMBOSSQBank.html.gz and manus-routes.json in the repository root")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "tests").mkdir(parents=True)
    (OUT / "downloads").mkdir(parents=True)

    print("Reading the compressed source and extracting test records…", flush=True)
    with gzip.open(SOURCE, "rt", encoding="utf-8") as f:
        source = f.read()
    if source.count(ARRAY_MARKER) != 1:
        raise RuntimeError("Could not uniquely locate the source TESTS_LIST array")

    array_start = source.index(ARRAY_MARKER) + len(ARRAY_MARKER)
    array_start = skip_ws(source, array_start)
    if array_start >= len(source) or source[array_start] != "[":
        raise RuntimeError("TESTS_LIST is not a JSON array")

    decoder = json.JSONDecoder()
    metadata: list[dict] = []
    seen_test_ids: set[str] = set()
    question_count = 0
    pos = array_start + 1
    first = True
    array_end = None

    while True:
        pos = skip_ws(source, pos)
        if pos >= len(source):
            raise RuntimeError("Unexpected end of TESTS_LIST")
        if source[pos] == "]":
            array_end = pos + 1
            break
        if not first:
            if source[pos] != ",":
                raise RuntimeError(f"Expected comma between tests near byte/character {pos}")
            pos = skip_ws(source, pos + 1)
        test, pos = decoder.raw_decode(source, pos)
        test_id = str(test.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", test_id):
            raise RuntimeError(f"Unsafe or missing test ID: {test_id!r}")
        if test_id in seen_test_ids:
            raise RuntimeError(f"Duplicate test ID: {test_id}")
        seen_test_ids.add(test_id)

        questions = test.get("questions") or []
        if not isinstance(questions, list) or not questions:
            raise RuntimeError(f"Test {test_id} has no question array")
        question_count += len(questions)
        write_json(OUT / "tests" / f"{test_id}.json", test)

        question_index = []
        for q in questions:
            qid = str(q.get("id", ""))
            preview = plain_preview(q.get("raw_text") or q.get("text") or "")
            question_index.append({"id": qid, "preview": preview})
        meta = {k: v for k, v in test.items() if k != "questions"}
        meta["questions"] = []
        meta["_questionIndex"] = question_index
        metadata.append(meta)
        first = False
        if len(metadata) % 200 == 0:
            print(f"Prepared {len(metadata)} tests / {question_count} questions…", flush=True)

    if array_end is None:
        raise RuntimeError("Could not find the end of TESTS_LIST")
    if len(metadata) != EXPECTED_TESTS or question_count != EXPECTED_QUESTIONS:
        raise RuntimeError(
            f"Source count mismatch: tests={len(metadata)} (expected {EXPECTED_TESTS}), "
            f"questions={question_count} (expected {EXPECTED_QUESTIONS})"
        )

    metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    metadata_json = metadata_json.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = source[:array_start] + metadata_json + source[array_end:]

    # Each full payload is hosted as a static asset and cached after first use.
    helper = r'''const TEST_DATA_PROMISES = new Map();
async function ensureTestData(testId) {
  const t = ALL_TESTS[testId];
  if (!t) throw new Error("Test not found: " + testId);
  if (Array.isArray(t.questions) && t.questions.length) return t;
  let pending = TEST_DATA_PROMISES.get(testId);
  if (!pending) {
    pending = (async () => {
      const r = await fetch("./tests/" + encodeURIComponent(testId) + ".json", { cache: "force-cache" });
      if (!r.ok) throw new Error("HTTP " + r.status + " for test " + testId);
      const full = await r.json();
      if (!full || !Array.isArray(full.questions) || !full.questions.length) throw new Error("Invalid question data for " + testId);
      Object.assign(t, full);
      normalizeTest(t, 0);
      return t;
    })();
    TEST_DATA_PROMISES.set(testId, pending);
  }
  try { return await pending; }
  catch (e) { TEST_DATA_PROMISES.delete(testId); throw e; }
}
async function ensureTestsData(testIds) {
  const ids = Array.from(new Set(testIds)).filter((id) => {
    const t = ALL_TESTS[id];
    return t && !t.custom && (!Array.isArray(t.questions) || !t.questions.length);
  });
  let next = 0, done = 0;
  const worker = async () => {
    while (next < ids.length) {
      const id = ids[next++];
      await ensureTestData(id);
      done++;
      if (CURRENT_VIEW === "builder") $("bSummaryText").textContent = "Loading test data " + done + "/" + ids.length + "…";
    }
  };
  await Promise.all(Array.from({ length: Math.min(6, ids.length) }, worker));
}
'''
    page = replace_once(page, "async function loadTest(testId) {", helper + "async function loadTest(testId) {", "loadTest insertion point")

    old_fetch = '''const r = await fetch("/api/tests/" + encodeURIComponent(testId), { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const full = await r.json();
      Object.assign(t, full);
      normalizeTest(t, 0);'''
    page = replace_once(page, old_fetch, "await ensureTestData(testId);", "question API fetch")

    old_search = '''function searchAll(q) {
  const ql = q.toLowerCase(), tests = [], qids = [];
  TEST_ORDER.forEach((id) => {
    const t = ALL_TESTS[id];
    if ((t.title + " " + t.path.join(" ")).toLowerCase().includes(ql)) tests.push(t);
    t.questions.forEach((qq, i) => { if (qidOf(qq).toLowerCase() === ql) qids.push({ t: t, i: i, q: qq }); });
  });
  return { tests: tests, qids: qids };
}'''
    new_search = '''function searchAll(q) {
  const ql = q.toLowerCase(), tests = [], qids = [];
  TEST_ORDER.forEach((id) => {
    const t = ALL_TESTS[id];
    if ((t.title + " " + t.path.join(" ")).toLowerCase().includes(ql)) tests.push(t);
    (t._questionIndex || []).forEach((qq, i) => {
      if (String(qq.id).toLowerCase() === ql) qids.push({ t: t, i: i, q: { id: qq.id, raw_text: qq.preview || "" } });
    });
  });
  return { tests: tests, qids: qids };
}'''
    page = replace_once(page, old_search, new_search, "global search function")

    old_qid = '''function qidSearch(qid) {
  qid = String(qid || "").trim();
  if (!qid) return false;
  for (const id of TEST_ORDER) {
    const i = ALL_TESTS[id].questions.findIndex((q) => qidOf(q).trim() === qid);
    if (i >= 0) { openQuestion(id, i); toast("Opened QID " + qid); return true; }
  }
  toast("QID " + qid + " not found");
  return false;
}'''
    new_qid = '''function qidSearch(qid) {
  qid = String(qid || "").trim();
  if (!qid) return false;
  for (const id of TEST_ORDER) {
    const i = (ALL_TESTS[id]._questionIndex || []).findIndex((q) => String(q.id).trim() === qid);
    if (i >= 0) { openQuestion(id, i); toast("Opened QID " + qid); return true; }
  }
  toast("QID " + qid + " not found");
  return false;
}'''
    page = replace_once(page, old_qid, new_qid, "QID lookup")

    old_open = '''function openQuestion(testId, index) {
  if (!CURRENT_TEST || CURRENT_TEST.id !== testId || reviewingHistory) {
    if (CURRENT_VIEW === "quiz") pauseQuiz();
    if (!loadTest(testId)) return;
  }
  if (submitted) { switchView("results"); showResults(index); return; }
  startQuiz(index);
}'''
    new_open = '''async function openQuestion(testId, index) {
  if (!CURRENT_TEST || CURRENT_TEST.id !== testId || reviewingHistory) {
    if (CURRENT_VIEW === "quiz") pauseQuiz();
    if (!(await loadTest(testId))) return;
  }
  if (submitted) { switchView("results"); showResults(index); return; }
  startQuiz(index);
}'''
    page = replace_once(page, old_open, new_open, "openQuestion async loading")

    old_enter = '''const found = ALL_TESTS && TEST_ORDER.some((id) => ALL_TESTS[id].questions.some((q) => qidOf(q).trim() === value));'''
    new_enter = '''const found = ALL_TESTS && TEST_ORDER.some((id) => (ALL_TESTS[id]._questionIndex || []).some((q) => String(q.id).trim() === value));'''
    page = replace_once(page, old_enter, new_enter, "QID enter lookup")

    page = replace_once(
        page,
        'n.qcount = n.all.reduce((sum, id) => sum + ALL_TESTS[id].questions.length, 0);',
        'n.qcount = n.all.reduce((sum, id) => sum + ALL_TESTS[id].num_questions, 0);',
        "custom builder folder question counts",
    )
    page = replace_once(
        page,
        "'<span class=\"count\">' + t.questions.length + \" Q</span></label>\";",
        "'<span class=\"count\">' + t.num_questions + \" Q</span></label>\";",
        "custom builder test question counts",
    )

    old_update = '''function updateBuilder() {
  if (CURRENT_VIEW !== "builder") return;
  const cfg = readCfg();
  store.set(DRAFT_KEY, cfg);
  const pool = collectPool(cfg);'''
    new_update = '''let BUILDER_LOAD_REV = 0;
async function updateBuilder() {
  if (CURRENT_VIEW !== "builder") return;
  const cfg = readCfg();
  store.set(DRAFT_KEY, cfg);
  const revision = ++BUILDER_LOAD_REV;
  if (cfg.tests.length) {
    $("bStart").disabled = true;
    $("bSummaryText").textContent = "Loading selected test data…";
    try { await ensureTestsData(cfg.tests); }
    catch (e) {
      console.error("Builder question loading failed:", e);
      if (revision === BUILDER_LOAD_REV) {
        B.lastPool = [];
        $("bPool").textContent = "0";
        $("bSummaryText").textContent = "Could not load selected test data";
        $("bSummarySub").textContent = "Check your connection and try again.";
        $("bStart").disabled = true;
      }
      return;
    }
    if (revision !== BUILDER_LOAD_REV || CURRENT_VIEW !== "builder") return;
  }
  const pool = collectPool(cfg);'''
    page = replace_once(page, old_update, new_update, "async custom builder update")

    old_dashboard = '''    (bmN ? '<button class="qbtn" data-go="bookmarks">★ Bookmarks (' + bmN + ")</button>" : "");'''
    new_dashboard = '''    (bmN ? '<button class="qbtn" data-go="bookmarks">★ Bookmarks (' + bmN + ")</button>" : "") +
    '<a class="qbtn ghost" href="./downloads/AMBOSSQBank.html" download="AMBOSSQBank.html">Download full offline HTML</a>';'''
    page = replace_once(page, old_dashboard, new_dashboard, "offline download link")

    # Protect against accidentally publishing a second large data array or API route.
    if "fetch(\"/api/tests/" in page:
        raise RuntimeError("A server-only question API reference remains in the built HTML")
    (OUT / "index.html").write_text(page, encoding="utf-8")
    shutil.copyfile(ROUTES, OUT / "manus-routes.json")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    print("Writing the byte-identical standalone HTML download…", flush=True)
    with gzip.open(SOURCE, "rb") as src, (OUT / "downloads" / "AMBOSSQBank.html").open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)

    total_bytes = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    index_bytes = (OUT / "index.html").stat().st_size
    download_bytes = (OUT / "downloads" / "AMBOSSQBank.html").stat().st_size
    shard_count = len(list((OUT / "tests").glob("*.json")))
    if shard_count != EXPECTED_TESTS:
        raise RuntimeError(f"Expected {EXPECTED_TESTS} test shards, got {shard_count}")
    if download_bytes < 300_000_000:
        raise RuntimeError("Standalone offline download is unexpectedly small")
    if total_bytes >= MAX_SITE_BYTES:
        raise RuntimeError(f"Published site exceeds the 1 GB Pages limit: {total_bytes} bytes")
    print(json.dumps({
        "tests": len(metadata),
        "questions": question_count,
        "test_shards": shard_count,
        "index_html_bytes": index_bytes,
        "offline_html_bytes": download_bytes,
        "site_total_bytes": total_bytes,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
