"""The Assistant GM job's time limit can run out while the job still waits for
a runner (2026-10-05: two runs cancelled at 15 min without ever starting), so
the job's limit must leave room for that wait beyond the bot's own step."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_job_limit_leaves_room_to_wait_for_a_runner():
    text = (ROOT / ".github/workflows/assistant_gm.yml").read_text(encoding="utf-8")
    job = re.search(r"^  run:\n(?:    .*\n|\s*\n)*?    timeout-minutes: (\d+)$", text, re.M)
    step = re.search(r"- name: Run Assistant GM\n(?:        .*\n)*?        timeout-minutes: (\d+)$", text, re.M)
    assert job and step
    job_limit, step_limit = int(job[1]), int(step[1])
    assert step_limit <= 15  # a hung run still ends quickly
    assert job_limit - step_limit >= 15


def test_a_pages_deploy_that_misses_its_artifact_tries_again():
    """deploy-pages can look the artifact up before GitHub's API lists it ("Found 0
    artifact(s)": 17 of 18 red runs, Nov 10-25 2026), so both publishing workflows
    try again after a wait, and only the last try can fail the job."""
    for name in ("assistant_gm.yml", "dashboard.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        tries = re.findall(r"- id: (\w+)\n(?:        .*\n)*?        uses: actions/deploy-pages@v4", text)
        assert tries == ["deploy", "deploy_again", "deploy_last"], name
        assert text.count("continue-on-error: true\n        uses: actions/deploy-pages") == 2, name
        assert "sleep" in text and "steps.deploy_last.outputs.page_url" in text, name
