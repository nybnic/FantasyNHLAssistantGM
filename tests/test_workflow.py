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
