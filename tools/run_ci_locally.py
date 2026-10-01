"""Run the `run:` steps of a GitHub Actions workflow locally, exactly as written.

Exists because of a specific failure: the CI workflow was "verified locally" by
retyping its commands, the retyped version differed from the YAML by one path
prefix, and every CI run was red for weeks. This runs the text in the workflow
file itself - same working-directory, same shell flags - so a local pass means
the workflow's own script passes.

    python tools/run_ci_locally.py .github/workflows/ci.yml [job]

Steps with `uses:` are skipped (they set up the runner); everything else runs.
"""

import os
import subprocess
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    path = argv[1] if len(argv) > 1 else ".github/workflows/ci.yml"
    with open(os.path.join(REPO, path), encoding="utf-8") as fh:
        workflow = yaml.safe_load(fh)

    jobs = workflow.get("jobs") or {}
    wanted = argv[2:] or list(jobs)
    failed = []
    for job_name in wanted:
        job = jobs[job_name]
        runner = job.get("runs-on", "")
        if "windows" in str(runner):
            print(f"== skip job {job_name}: needs {runner}")
            continue
        print(f"== job {job_name}")
        for step in job.get("steps") or []:
            if "run" not in step:
                continue
            name = step.get("name", step["run"].splitlines()[0])
            cwd = os.path.join(REPO, step.get("working-directory", "."))
            # GitHub runs bash steps as `bash -e {0}`; match it so `set -e`
            # behaviour - which decides whether a failure stops the step - is the same.
            result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd)
            status = "ok" if result.returncode == 0 else f"FAILED ({result.returncode})"
            print(f"   [{status}] {name}")
            if result.returncode != 0:
                failed.append(f"{job_name}: {name}")
    if failed:
        print("\nFailed steps:\n  " + "\n  ".join(failed))
        return 1
    print("\nAll run steps passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
