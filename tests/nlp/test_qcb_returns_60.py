"""iDEAL 6.0: the generated QCB return embeddings (F010, F013, F014, F015) end to end.

Scope: behaviour that is IMPLEMENTED TODAY on the 6.0 host. It does not test the planned
return-first resolver, Best Guess or QA guards - those do not exist yet.

Two layers:

1. Index/data integrity - reads artifacts/nlp-index/ideal-60 directly (no app code, no model):
   QA pairs against schema.json, SQL validity, duplicates, per-return coverage, and that the
   JSON, the pickled metadata and the FAISS index agree and match build_stamp.json.

2. Pipeline behaviour - tests/nlp/_qcb60_probe.py runs the REAL pipeline in a fresh interpreter
   with VERSION=6.0 (settings are read once at import, and .env pins 5.5, so this cannot be done
   in-process). Needs the 6.0 tenant repository (DV_BASE_PATH_60, default D:\\Repo6), the
   embedding model, and a login whose department has the four QCB returns
   (QCB_TEST_LOGIN, default vaibhav@irisindia.net, tenant QCB_TEST_TENANT=1001). Skipped
   otherwise. No Oracle and no LLM.

KNOWN GAPS are asserted as xfail so they are reported, not hidden.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import pickle
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX = os.path.join(ROOT, "artifacts", "nlp-index", "ideal-60")
PROBE = os.path.join(ROOT, "tests", "nlp", "_qcb60_probe.py")

RETURNS = {  # code -> (return_id in the 6.0 repo, return_name in schema.json)
    "F010": ("4091", "QCB_F010_Maturity Ladder of Assets & Liabilities"),
    "F013": ("4079", "QCB_F013_Breakdown of exposures by geography"),
    "F014": ("4080", "QCB_F014_Breakdown of Funding by geography"),
    "F015": ("4092", "QCB_F015_Foreign Currency Net Open Positions"),
}
RID = {code: v[0] for code, v in RETURNS.items()}

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(INDEX, "qa_pairs.json")), reason="6.0 index folder not present",
)


def _load(name):
    with open(os.path.join(INDEX, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def qa():
    return _load("qa_pairs.json")


@pytest.fixture(scope="module")
def schema():
    return _load("schema.json")


# ═══ 1. Index / data integrity ═══════════════════════════════════════════════════════════════

class TestQaDataIntegrity:
    def test_every_pair_has_exactly_the_expected_fields(self, qa):
        assert qa and all(set(p) == {"table", "question", "sql", "return_name"} for p in qa)
        assert all(str(p[k]).strip() for p in qa for k in p)

    def test_return_names_match_schema_json(self, qa, schema):
        owner = {t["table"]: t["return_name"] for t in schema}
        assert [p["table"] for p in qa if p["table"] not in owner] == []
        assert [p for p in qa if owner[p["table"]] != p["return_name"]] == []

    def test_return_names_are_the_four_expected_returns(self, qa):
        assert {p["return_name"] for p in qa} == {v[1] for v in RETURNS.values()}

    def test_per_return_coverage(self, qa):
        by = collections.Counter(p["return_name"][4:8] for p in qa)
        assert set(by) == set(RETURNS)
        # Floors, not exact counts: a rebuild may add pairs but must not thin a return out.
        assert by["F010"] >= 1000 and by["F015"] >= 500 and by["F014"] >= 200 and by["F013"] >= 80

    def test_every_data_table_with_pairs_is_covered(self, qa):
        tables = {p["table"] for p in qa}
        for t in ("qcb_f010_rcm_in_qtr", "qcb_f010_rcm_out_qtr", "qcb_f013_break_exposure",
                  "qcb_f014_funding_geo", "qcb_f015_foreign_cur_nop"):
            assert t in tables

    def test_no_duplicate_questions(self, qa):
        counts = collections.Counter(p["question"].strip().lower() for p in qa)
        assert [q for q, n in counts.items() if n > 1] == []

    def test_no_question_belongs_to_two_returns(self, qa):
        owners = collections.defaultdict(set)
        for p in qa:
            owners[p["question"].strip().lower()].add(p["return_name"])
        assert [q for q, r in owners.items() if len(r) > 1] == []

    def test_no_duplicate_question_sql_pairs(self, qa):
        assert len({(p["question"], p["sql"]) for p in qa}) == len(qa)

    def test_sql_selects_from_its_own_table_with_real_columns_only(self, qa, schema):
        cols = {t["table"]: {c["name"].lower() for c in t["columns"]} for t in schema}
        sql_words = set(
            "select from where and or max rdate as in like group by order sum count is null not "
            "distinct desc asc fetch first rows only row between abs round nvl case when then else "
            "end avg min upper lower".split()
        )
        bad = []
        for p in qa:
            m = re.search(r"\bFROM\s+(\w+)", p["sql"], re.I)
            if not m or m.group(1).lower() != p["table"]:
                bad.append(("wrong-from", p["sql"]))
                continue
            body = re.sub(r"'(?:[^']|'')*'", "", p["sql"])
            idents = {w.lower() for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", body)}
            unknown = {i for i in idents - sql_words - {p["table"]} - cols[p["table"]] if not i.isdigit()}
            if unknown:
                bad.append((unknown, p["sql"]))
        assert bad == []

    def test_sql_is_read_only_and_single_statement(self, qa):
        assert all(re.match(r"^\s*SELECT\b", p["sql"], re.I) and ";" not in p["sql"] for p in qa)
        assert not [p for p in qa if re.search(r"\b(insert|update|delete|drop|alter|merge|truncate)\b", p["sql"], re.I)]

    def test_sql_asks_for_the_latest_period(self, qa):
        data = [p for p in qa if not p["table"].endswith("_filing_info")]
        assert all("MAX(RDATE)" in p["sql"].upper() for p in data)

    def test_most_questions_name_their_return(self, qa):
        """Return-name QA pairs: a majority carry the code or a recognisable phrase."""
        cue = re.compile(r"f0\d\d|maturity ladder|exposures? by geography|funding by geography|net open position", re.I)
        assert sum(1 for p in qa if cue.search(p["question"])) / len(qa) > 0.6

    def test_return_codes_in_questions_point_at_the_right_return(self, qa):
        wrong = [p for p in qa
                 for code in re.findall(r"\bF0(?:10|13|14|15)\b", p["question"], re.I)
                 if code.upper() != p["return_name"][4:8]]
        assert wrong == []


class TestIndexConsistency:
    def test_qa_json_meta_and_faiss_agree(self, qa):
        faiss = pytest.importorskip("faiss")
        with open(os.path.join(INDEX, "qa_meta.pkl"), "rb") as fh:
            meta = pickle.load(fh)  # trusted, repo-shipped artifact
        index = faiss.read_index(os.path.join(INDEX, "qa_index.faiss"))
        assert len(meta) == len(qa) == index.ntotal
        assert all(m["question"] == p["question"] and m["sql"] == p["sql"] and m["table"] == p["table"]
                   for m, p in zip(meta, qa))

    def test_all_index_files_exist_and_match_build_stamp(self):
        stamp = _load("build_stamp.json")
        assert stamp["qa_pairs"] == len(_load("qa_pairs.json"))
        def _sha(name):
            with open(os.path.join(INDEX, name), "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()

        assert [f for f, digest in stamp["checksums"].items() if _sha(f) != digest] == []

    def test_stamp_lists_the_four_returns(self):
        assert set(_load("build_stamp.json")["returns"]) == {v[1] for v in RETURNS.values()}

    def test_build_report_is_clean(self):
        report = _load("build_report.json")
        guard = report["superset_guard"]
        assert guard["ok"] and not guard["missing_from_schema_json"] and not guard["not_yet_registered"]
        assert report["qa"]["duplicates_dropped"] == []
        assert report["concept_map"]["schema_validated"] is True
        assert not report["concept_map"]["orphan_metric_tables"]

    def test_table_index_holds_all_schema_tables(self, schema):
        faiss = pytest.importorskip("faiss")
        with open(os.path.join(INDEX, "table_meta.pkl"), "rb") as fh:
            meta = pickle.load(fh)
        index = faiss.read_index(os.path.join(INDEX, "table_index.faiss"))
        assert index.ntotal == len(meta) == len(schema)
        assert {m["table"].lower() for m in meta} == {t["table"] for t in schema}


# ═══ 2. Pipeline behaviour on the real 6.0 host ═════════════════════════════════════════════

LOGIN = os.environ.get("QCB_TEST_LOGIN", "vaibhav@irisindia.net")
TENANT = os.environ.get("QCB_TEST_TENANT", "1001")


@pytest.fixture(scope="module")
def probe():
    repo = os.environ.get("DV_BASE_PATH_60") or r"D:\Repo6"
    if not os.path.isfile(os.path.join(repo, TENANT, "DataBase", "Return.xml")):
        pytest.skip(f"6.0 tenant repository not found at {repo}")
    pytest.importorskip("faiss")
    env = {
        **os.environ, "VERSION": "6.0", "DV_DOTENV_OVERRIDE": "false", "DV_AUTH_ENABLED": "true",
        "DV_NLP_WARMUP": "false", "QCB_PROBE_LOGIN": LOGIN, "QCB_PROBE_TENANT": TENANT,
    }
    for key in ("DV_NLP_INDEX_DIR", "DV_NLP_QA_FASTPATH"):
        env.pop(key, None)
    done = subprocess.run([sys.executable, PROBE], capture_output=True, text=True, env=env, cwd=ROOT, timeout=900, check=False)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("@@JSON@@")), None)
    if line is None:
        pytest.skip("probe could not run (model/index unavailable?): " + done.stderr[-400:])
    data = json.loads(line[len("@@JSON@@"):])
    if not set(RID.values()) <= set(data["allowed"]):
        pytest.skip(f"{LOGIN} is not allowed the QCB returns; assign them in Department.xml first")
    return data


class TestHostAndCoverage:
    def test_runs_as_6_0_on_the_6_0_index_with_auth_on(self, probe):
        assert probe["version"] == "6.0" and probe["auth_enabled"] is True
        assert probe["index_dir"].replace("\\", "/").rstrip("/").endswith("nlp-index/ideal-60")

    @pytest.mark.parametrize("table,code", [
        ("qcb_f010_filing_info", "F010"), ("qcb_f010_rcm_in_qtr", "F010"), ("qcb_f010_rcm_out_qtr", "F010"),
        ("qcb_f013_filing_info", "F013"), ("qcb_f013_break_exposure", "F013"),
        ("qcb_f014_filing_info", "F014"), ("qcb_f014_funding_geo", "F014"),
        ("qcb_f015_filing_info", "F015"), ("qcb_f015_foreign_cur_nop", "F015"),
    ])
    def test_table_resolves_to_its_return_id(self, probe, table, code):
        assert probe["lookup"][table] == RID[code]

    def test_index_covers_exactly_the_four_returns(self, probe):
        assert probe["coverage"] == sorted(RID.values())

    def test_tables_by_return(self, probe):
        t = {k: {x.lower() for x in v} for k, v in probe["tables_by_return"].items()}
        assert t["4091"] == {"qcb_f010_filing_info", "qcb_f010_rcm_in_qtr", "qcb_f010_rcm_out_qtr"}
        assert t["4079"] == {"qcb_f013_filing_info", "qcb_f013_break_exposure"}
        assert t["4080"] == {"qcb_f014_filing_info", "qcb_f014_funding_geo"}
        assert t["4092"] == {"qcb_f015_filing_info", "qcb_f015_foreign_cur_nop"}

    def test_unmapped_index_table_never_reaches_a_shortlist(self, probe):
        assert probe["lookup"]["qcb_f015_bank_cap_base"] is None
        assert probe["unmapped_table_in_any_result"] is False

    @pytest.mark.xfail(reason="KNOWN GAP: qcb_f015_bank_cap_base is in schema.json and the index but not in "
                              "return 4092's TableMapping.xml, so it cannot be resolved or queried", strict=True)
    def test_bank_cap_base_resolves_to_f015(self, probe):
        assert probe["lookup"]["qcb_f015_bank_cap_base"] == RID["F015"]


class TestExplicitReturns:
    @pytest.mark.parametrize("query,code", [
        ("F010", "F010"), ("show maturity ladder", "F010"),
        ("country wise funding", "F014"), ("net open position in USD", "F015"),
        ("breakdown of exposures by geography", "F013"),
    ])
    def test_analyzer_names_the_return(self, probe, query, code):
        assert probe["analysis"][query]["return_ids"] == [RID[code]]

    @pytest.mark.parametrize("query,code", [
        ("F010", "F010"), ("show maturity ladder", "F010"),
        ("country wise funding", "F014"), ("net open position in USD", "F015"),
        ("breakdown of exposures by geography", "F013"),
    ])
    def test_retrieval_is_scoped_to_the_named_return(self, probe, query, code):
        r = probe["retrieval"][query]
        assert r["tables"] and set(r["return_ids"]) == {RID[code]}

    @pytest.mark.parametrize("query", [
        "F010", "show maturity ladder", "country wise funding",
        "net open position in USD", "breakdown of exposures by geography",
    ])
    def test_a_named_return_is_not_asked_about(self, probe, query):
        r = probe["retrieval"][query]
        assert not r["ambiguous"] and r["confidence"] >= 0.60  # CONFIDENCE_AUTO_PROCEED

    def test_data_table_outranks_filing_info_for_a_metric_query(self, probe):
        assert probe["retrieval"]["net open position in USD"]["tables"][0] == "qcb_f015_foreign_cur_nop"
        assert probe["retrieval"]["country wise funding"]["tables"][0] == "qcb_f014_funding_geo"
        assert probe["retrieval"]["breakdown of exposures by geography"]["tables"][0] == "qcb_f013_break_exposure"

    def test_bare_code_resolves_the_return_even_though_filing_info_ranks_first(self, probe):
        r = probe["retrieval"]["F010"]
        assert r["tables"][0] == "qcb_f010_filing_info" and set(r["return_ids"]) == {RID["F010"]}


class TestImplicitAndAmbiguous:
    def test_implicit_query_names_no_return(self, probe):
        for q in ("assets maturing overnight", "forex exposure limit", "total staff"):
            assert probe["analysis"][q]["return_ids"] == []

    def test_implicit_f010_query_ranks_f010_tables_first(self, probe):
        r = probe["retrieval"]["assets maturing overnight"]
        assert set(r["return_ids"][:2]) == {RID["F010"]}

    def test_unrelated_query_is_flagged_ambiguous_and_spans_returns(self, probe):
        r = probe["retrieval"]["total staff"]
        assert r["ambiguous"] is True and len(set(r["return_ids"])) > 1

    def test_unrelated_query_gets_a_table_clarification_not_an_answer(self, probe):
        body = probe["route"]["ambiguous_total_staff"]
        assert body["kind"] == "response"
        body = body["body"]
        assert body["needs_clarification"] is True and body["dimension"] == "table"
        assert body["skippable"] is True and body["resolved_context"] == {"query": "total staff"}
        assert {o["id"] for o in body["options"]} <= {
            "qcb_f010_rcm_in_qtr", "qcb_f010_rcm_out_qtr", "qcb_f013_break_exposure",
            "qcb_f014_funding_geo", "qcb_f015_foreign_cur_nop",
        }
        assert body["interpretation"]["return_names"] == []

    @pytest.mark.xfail(reason="KNOWN GAP (planned return-first work): an implicit query with no return cue "
                              "is ranked across all returns, so 'forex exposure limit' is confidently "
                              "answered from F013 instead of F015", strict=True)
    def test_forex_exposure_limit_resolves_to_f015(self, probe):
        assert probe["retrieval"]["forex exposure limit"]["return_ids"][0] == RID["F015"]

    @pytest.mark.xfail(reason="KNOWN GAP: 'assets maturing overnight' is clearly F010; the ambiguity is only "
                              "in/out-of-Qatar, but the user gets a cross-return table prompt", strict=True)
    def test_overnight_assets_not_ambiguous_across_returns(self, probe):
        assert set(probe["retrieval"]["assets maturing overnight"]["return_ids"]) == {RID["F010"]}


class TestQaFastPath:
    def test_qa_questions_are_matched_to_their_own_table(self, probe):
        s = probe["qa"]["stats"]
        assert s["sampled"] == 60 and s["matched"] >= 50
        assert s["matched_right_table"] == s["matched"]
        assert s["matched_right_return"] == s["matched"]

    def test_fast_path_never_answers_from_a_different_table(self, probe):
        assert probe["qa"]["stats"]["fast_wrong_table"] == 0

    def test_fast_path_answers_most_matched_questions(self, probe):
        s = probe["qa"]["stats"]
        assert s["fast_ok"] / s["matched"] >= 0.80  # the rest fall back to the LLM path

    def test_fast_path_works_for_every_returns_data(self, probe):
        pr = probe["qa"]["stats"]["per_return"]
        assert all(pr[c]["fast_ok"] >= 1 for c in pr if pr[c]["matched"] >= 3)

    def test_fast_path_guards(self, probe):
        g = probe["qa"]["guard"]
        assert g and g["return_id"] in set(RID.values()) and g["columns"]
        assert g["table_not_in_shortlist"] is None   # not authorized/retrieved -> no answer
        assert g["column_missing"] is None           # never a partial column set
        assert g["no_match"] is None
        assert g["disabled_flag"] is None            # DV_NLP_QA_FASTPATH=false


class TestClarificationFlow:
    def test_named_return_reaches_intent_resolution_scoped(self, probe):
        r = probe["route"]["named_f014"]
        assert r["kind"] == "reached_intent" and r["return_ids"] == [RID["F014"]]

    def test_return_answer_scopes_to_that_return(self, probe):
        r = probe["route"]["return_answer_f015"]
        assert r["kind"] == "reached_intent" and r["return_ids"] == [RID["F015"]]
        assert {t.lower() for t in r["tables"]} == {"qcb_f015_foreign_cur_nop", "qcb_f015_filing_info"}

    def test_table_answer_pins_that_one_table(self, probe):
        r = probe["route"]["table_answer"]
        assert r["kind"] == "reached_intent" and r["tables"] == ["qcb_f010_rcm_in_qtr"]

    def test_return_skip_proceeds_with_the_unscoped_shortlist(self, probe):
        """Today's '__skip__' is a silent best-effort across returns (the planned Best Guess replaces
        the silence with a labelled suggestion). Documented so the change is deliberate."""
        r = probe["route"]["return_skip"]
        assert r["kind"] == "reached_intent" and len(r["return_ids"]) > 1

    def test_malformed_clarification_id_is_a_400(self, probe):
        assert probe["route"]["bad_return_id_shape"] == {"kind": "http", "status": 400}

    def test_empty_query_is_a_400(self, probe):
        assert probe["route"]["empty_query"] == {"kind": "http", "status": 400}


class TestAuthorization:
    def test_login_has_the_four_returns_but_unknown_login_has_nothing(self, probe):
        assert set(RID.values()) <= set(probe["allowed"])
        assert probe["allowed_unknown_login"] == []

    def test_retrieval_only_ever_returns_allowed_returns(self, probe):
        for q, rids in probe["restricted_retrieval"].items():
            assert set(rids) <= set(probe["restricted_allowed"]), q

    def test_unauthorized_return_answer_is_403(self, probe):
        assert probe["route"]["restricted_answer_unauthorized_return"] == {"kind": "http", "status": 403}

    def test_unauthorized_table_answer_is_403(self, probe):
        assert probe["route"]["restricted_table_answer_unauthorized"] == {"kind": "http", "status": 403}

    def test_unauthorized_pinned_return_is_403_even_via_skip(self, probe):
        assert probe["route"]["restricted_pinned_unauthorized"] == {"kind": "http", "status": 403}

    def test_authorized_return_answer_still_works(self, probe):
        r = probe["route"]["restricted_answer_authorized_return"]
        assert r["kind"] == "reached_intent" and r["return_ids"] == [RID["F010"]]

    @pytest.mark.xfail(reason="KNOWN GAP (plan requirement 5): when the user may not see the return they NAMED, "
                              "retrieval silently answers from a return they ARE allowed (F010 for an F013 "
                              "question) instead of saying access is missing", strict=True)
    def test_named_unauthorized_return_is_not_silently_answered_from_another(self, probe):
        assert probe["restricted_retrieval"]["breakdown of exposures by geography"] == []
