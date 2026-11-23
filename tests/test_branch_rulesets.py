"""ci/github/rulesets/: the branch rules the repository owner imports into GitHub
(ci/jenkins/README.md, "Branch rules on GitHub").

A required check that no pull request runs never reports, and GitHub then blocks
every merge into develop and main. So every required check must be the name of a
job that runs, unconditionally, on every pull request to both branches. The
workflows are read line by line (PyYAML is not a dependency); they use the plain
block style this expects.
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULESETS = os.path.join(ROOT, "ci", "github", "rulesets")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
GITHUB_ACTIONS_APP_ID = 15368


def _ruleset(name):
    with open(os.path.join(RULESETS, name), encoding="utf-8") as f:
        return json.load(f)


def _rule(ruleset, rule_type):
    return next((r for r in ruleset["rules"] if r["type"] == rule_type), None)


def _block(text, key, indent=""):
    """The lines under `key:` at `indent`, up to the next key at that indent."""
    m = re.search(rf"^{indent}{key}:[ \t]*\n((?:{indent}[ \t]+.*\n|[ \t]*#.*\n|[ \t]*\n)*)", text, re.M)
    return m.group(1) if m else ""


def _runs_on_every_pr_to(text, branches):
    pr = _block(_block(text, "on"), "pull_request", "  ")
    if not pr or re.search(r"^\s+paths(-ignore)?:", pr, re.M):
        return False
    listed = re.search(r"branches:\s*\[([^\]]*)\]", pr)
    return listed is None or set(branches) <= {b.strip().strip("'\"") for b in listed.group(1).split(",")}


def _unconditional_jobs(text):
    """Display names of the workflow's jobs that have no job-level `if:`."""
    names = []
    for chunk in re.split(r"^  [\w-]+:[ \t]*\n", _block(text, "jobs"), flags=re.M)[1:]:
        name = re.search(r"^    name:\s*(.+?)\s*$", chunk, re.M)
        if name and not re.search(r"^    if:", chunk, re.M):
            names.append(name.group(1).strip("'\""))
    return names


def _checks_every_pr_runs():
    names = set()
    for fn in sorted(os.listdir(WORKFLOWS)):
        if fn.endswith((".yml", ".yaml")):
            with open(os.path.join(WORKFLOWS, fn), encoding="utf-8") as f:
                text = f.read()
            if _runs_on_every_pr_to(text, ("develop", "main")):
                names.update(_unconditional_jobs(text))
    return names


class TestDevelopAndMain:
    rs = _ruleset("develop-and-main.json")

    def test_it_covers_develop_and_main_and_is_on(self):
        assert self.rs["target"] == "branch" and self.rs["enforcement"] == "active"
        assert sorted(self.rs["conditions"]["ref_name"]["include"]) == ["refs/heads/develop", "refs/heads/main"]

    def test_changes_only_through_prs_and_nobody_bypasses(self):
        assert _rule(self.rs, "pull_request") and _rule(self.rs, "deletion") and _rule(self.rs, "non_fast_forward")
        assert self.rs["bypass_actors"] == []

    def test_every_required_check_is_a_job_every_pr_runs(self):
        required = [c["context"] for c in _rule(self.rs, "required_status_checks")["parameters"]["required_status_checks"]]
        assert required and len(required) == len(set(required))
        missing = sorted(set(required) - _checks_every_pr_runs())
        assert missing == [], f"no workflow runs these on every PR to develop and main: {missing}"

    def test_checks_must_come_from_github_actions(self):
        """Pinned to the GitHub Actions app, so a commit status with the same
        name posted by anything else does not satisfy the rule."""
        for check in _rule(self.rs, "required_status_checks")["parameters"]["required_status_checks"]:
            assert check.get("integration_id") == GITHUB_ACTIONS_APP_ID, check["context"]

    def test_the_parser_sees_the_real_workflows(self):
        """Guards the test above from passing because the parser found nothing."""
        runs = _checks_every_pr_runs()
        assert {"Pytest", "Podman build", "Security & Code Quality Audit"} <= runs
        assert "Mobile jest" not in runs                         # only for mobile/ changes
        assert "Alert security ops on gate failure" not in runs  # if: failure()


class TestProduction:
    rs = _ruleset("production.json")

    def test_it_can_not_be_deleted(self):
        assert self.rs["enforcement"] == "active"
        assert self.rs["conditions"]["ref_name"]["include"] == ["refs/heads/production"]
        assert _rule(self.rs, "deletion")

    @pytest.mark.parametrize("rule", ["non_fast_forward", "pull_request", "required_status_checks"])
    def test_jenkins_can_still_move_it(self, rule):
        """ci/jenkins/deploy_prod.ps1 force-pushes the approved commit, and a
        rollback is a force-push too."""
        assert _rule(self.rs, rule) is None
