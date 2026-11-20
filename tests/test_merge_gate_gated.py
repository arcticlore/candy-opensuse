#!/usr/bin/env python3
"""clear-gated: снятие редундантного action_required run'а на bot-PR.

GitHub заводит на PR, созданный GITHUB_TOKEN, пустой `pull_request` run и
гасит его как action_required. Jobs в нём нет (required contexts приходят из
явного dispatch), но merge box остаётся BLOCKED и `--auto` merge не наступает.
Эти тесты фиксируют контракт снятия: узкая выборка, DELETE с фолбэком на
approve, и fail-closed на любом отказе/таймауте.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("merge_gate", ROOT / "bin" / "merge_gate.py")
mg = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mg)

BRANCH = "candy-bot/publish-9c46edb3d475"
SHA = "d997b6a792198f9a62a9069b4a2288d73b60b8fb"


def run_payload(**over) -> dict:
    data = {
        "id": 44738315930,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "action_required",
        "head_sha": SHA,
        "head_branch": BRANCH,
    }
    data.update(over)
    return data


def make_fake_gh(tmp: Path, state: dict) -> str:
    """Офлайновый `gh`: читает/меняет JSON-состояние, без сети и без токена."""
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    script = tmp / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        f"S={str(state_path)!r}\n"
        "st=json.load(open(S))\n"
        "a=sys.argv[1:]\n"
        "def save():\n"
        "    json.dump(st,open(S,'w'))\n"
        "if a[:2]==['api','-X'] and a[2]=='DELETE':\n"
        "    if not st.get('allow_delete'):\n"
        "        print('HTTP 403',file=sys.stderr); sys.exit(1)\n"
        "    rid=int(a[3].rsplit('/',1)[1])\n"
        "    st['runs']=[r for r in st['runs'] if r['id']!=rid]\n"
        "    st.setdefault('deleted',[]).append(rid); save(); print('{}'); sys.exit(0)\n"
        "if a[:2]==['api','-X'] and a[2]=='POST' and a[3].endswith('/approve'):\n"
        "    if not st.get('allow_approve'):\n"
        "        print('HTTP 403',file=sys.stderr); sys.exit(1)\n"
        "    rid=int(a[3].rsplit('/',2)[1])\n"
        "    for r in st['runs']:\n"
        "        if r['id']==rid: r['conclusion']=None; r['status']='queued'\n"
        "    st.setdefault('approved',[]).append(rid); save(); print('{}'); sys.exit(0)\n"
        "if a[0]=='api' and 'actions/runs?head_sha=' in a[1]:\n"
        "    st['list_calls']=st.get('list_calls',0)+1; save()\n"
        "    print(json.dumps({'workflow_runs':st['runs']})); sys.exit(0)\n"
        "print('unexpected call: '+' '.join(a),file=sys.stderr); sys.exit(2)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


class ClearGatedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(os.environ.get("PYTEST_TMPDIR", "")) if False else None
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def state(self, runs, **over) -> dict:
        base = {"runs": runs, "allow_delete": True, "allow_approve": True}
        base.update(over)
        return base

    def test_selects_only_the_gated_bot_run(self) -> None:
        runs = [
            run_payload(),
            run_payload(id=1, event="workflow_dispatch", conclusion="success"),
            run_payload(id=2, conclusion="success"),
            run_payload(id=3, head_branch="candy-bot/other"),
            run_payload(id=4, event="push", conclusion="action_required"),
        ]
        gh = make_fake_gh(self.tmp, self.state(runs))
        found = mg.gated_runs("o/r", SHA, BRANCH, gh=gh)
        self.assertEqual([r["id"] for r in found], [44738315930])

    def test_delete_path_clears_run(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()]))
        handled = mg.clear_gated_runs("o/r", SHA, BRANCH, gh=gh, sleep=lambda _s: None)
        self.assertEqual(handled, [("deleted", 44738315930)])
        left = mg.gated_runs("o/r", SHA, BRANCH, gh=gh)
        self.assertEqual(left, [])

    def test_approve_is_the_fallback_when_delete_is_denied(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()], allow_delete=False))
        handled = mg.clear_gated_runs("o/r", SHA, BRANCH, gh=gh, sleep=lambda _s: None)
        self.assertEqual(handled, [("approved", 44738315930)])
        self.assertEqual(mg.gated_runs("o/r", SHA, BRANCH, gh=gh), [])

    def test_fails_closed_when_neither_delete_nor_approve_works(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()],
                                               allow_delete=False, allow_approve=False))
        with self.assertRaises(mg.GateError) as ctx:
            mg.clear_gated_runs("o/r", SHA, BRANCH, gh=gh, sleep=lambda _s: None)
        self.assertIn("не снят", str(ctx.exception))

    def test_noop_when_nothing_is_gated(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload(conclusion="success")]))
        self.assertEqual(
            mg.clear_gated_runs("o/r", SHA, BRANCH, gh=gh, sleep=lambda _s: None), [])

    def test_foreign_head_sha_in_response_is_fatal(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload(head_sha="0" * 40)]))
        with self.assertRaises(mg.GateError):
            mg.gated_runs("o/r", SHA, BRANCH, gh=gh)

    def test_branch_outside_candy_bot_is_refused(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()]))
        for bad in ("master", "fix/x", "candy-bot", "../candy-bot/x"):
            with self.assertRaises(mg.GateError):
                mg.gated_runs("o/r", SHA, bad, gh=gh)

    def test_empty_head_sha_is_refused(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()]))
        with self.assertRaises(mg.GateError):
            mg.gated_runs("o/r", "", BRANCH, gh=gh)

    def test_timeout_is_fail_closed_when_runs_keep_reappearing(self) -> None:
        # DELETE «успешен», но GitHub отдаёт gated run снова: ждать вечно нельзя.
        runs = [run_payload()]
        gh = make_fake_gh(self.tmp, self.state(runs, allow_delete=False, allow_approve=True))
        state_file = self.tmp / "state.json"

        def resurrect(_s: float) -> None:
            st = json.loads(state_file.read_text())
            st["runs"] = [run_payload()]
            state_file.write_text(json.dumps(st))

        ticks = iter([0.0, 10.0, 999.0, 1000.0, 1001.0])
        with self.assertRaises(mg.GateError) as ctx:
            mg.clear_gated_runs("o/r", SHA, BRANCH, gh=gh, timeout_min=1.0,
                                sleep=resurrect, clock=lambda: next(ticks))
        self.assertIn("таймаут", str(ctx.exception))

    def test_cli_exposes_clear_gated(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()]))
        rc = mg.main(["clear-gated", "--repo", "o/r", "--branch", BRANCH,
                      "--head-sha", SHA, "--gh", gh])
        self.assertEqual(rc, 0)

    def test_cli_returns_one_on_gate_error(self) -> None:
        gh = make_fake_gh(self.tmp, self.state([run_payload()],
                                               allow_delete=False, allow_approve=False))
        rc = mg.main(["clear-gated", "--repo", "o/r", "--branch", BRANCH,
                      "--head-sha", SHA, "--gh", gh])
        self.assertEqual(rc, 1)


class WorkflowWiringTests(unittest.TestCase):
    """Гейт обязан быть включён в волну, а не просто существовать в bin/."""

    TEXT = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    def test_update_yml_calls_clear_gated(self) -> None:
        self.assertIn("merge_gate.py clear-gated", self.TEXT)

    def test_clear_gated_runs_before_auto_merge(self) -> None:
        first = self.TEXT.index("merge_gate.py clear-gated")
        merge = self.TEXT.index("- name: Enable auto-merge after green checks")
        self.assertLess(first, merge)

    def test_auto_merge_depends_on_the_gate(self) -> None:
        idx = self.TEXT.index("- name: Enable auto-merge after green checks")
        window = self.TEXT[idx:idx + 500]
        self.assertIn("steps.clear-gated.outcome == 'success'", window)

    def test_clear_gated_is_bound_to_exact_pr_head(self) -> None:
        idx = self.TEXT.index("merge_gate.py clear-gated")
        window = self.TEXT[idx:idx + 400]
        self.assertIn("--head-sha", window)
        self.assertIn("--branch", window)

    def test_clear_gated_failure_is_not_swallowed(self) -> None:
        idx = self.TEXT.index("merge_gate.py clear-gated")
        window = self.TEXT[idx - 600:idx + 400]
        self.assertNotIn("continue-on-error: true", window)
        self.assertNotIn("|| true", window)


if __name__ == "__main__":
    sys.exit(unittest.main())
